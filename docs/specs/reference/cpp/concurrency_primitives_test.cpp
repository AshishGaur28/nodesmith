// Build & run (from this directory):
//   c++ -std=c++17 -O1 -g -fsanitize=thread -pthread concurrency_primitives_test.cpp -o r2d_test && ./r2d_test
#include <cassert>
#include <cstdio>
#include <string>
#include <thread>
#include <vector>

#include "concurrency_primitives.hpp"

struct Params {
  double a{0}, b{0};       // invariant: a + b == 100
  std::string mode{"x"};   // non-trivial member: copies allocate, readers never do
};

int main() {
  // 1. Snapshot ring: readers never see a torn / half-applied multi-parameter update.
  r2d::SnapshotRing<Params, 6> ring(Params{40, 60, "x"});
  std::atomic<bool> stop{false};
  std::atomic<long> reads{0}, torn{0};
  std::vector<std::thread> readers;
  for (int t = 0; t < 4; ++t)
    readers.emplace_back([&] {
      while (!stop.load()) {
        auto g = ring.acquire();
        if (g->a + g->b != 100.0) ++torn;
        if (g->mode.empty() || g->mode.size() > 5) ++torn;  // string member is intact too
        ++reads;
      }
    });
  long published = 0, busy = 0;
  for (int i = 0; i < 20000; ++i) {
    const double a = i % 101;
    const bool ok = ring.publish([&](Params & p) { p.a = a; p.b = 100.0 - a; p.mode = std::string(1 + i % 5, 'm'); });
    ok ? ++published : ++busy;
  }
  stop = true;
  for (auto & r : readers) r.join();
  std::printf("ring: published=%ld busy=%ld reads=%ld torn=%ld\n", published, busy, reads.load(), torn.load());
  assert(torn == 0 && published > 0);

  // 1b. All other slots pinned -> publish refuses (returns false) and leaves the current snapshot intact.
  {
    r2d::SnapshotRing<Params, 3> small(Params{1, 99, "s"});
    auto pin1 = small.acquire();
    assert(small.publish([](Params & p) { p.a = 2; p.b = 98; }));   // moves current to slot 1
    auto pin2 = small.acquire();                                     // pins slot 1
    assert(small.publish([](Params & p) { p.a = 3; p.b = 97; }));   // slot 2 free
    auto pin3 = small.acquire();                                     // pins slot 2; slot 0 pinned by pin1
    assert(!small.publish([](Params & p) { p.a = 4; p.b = 96; }));  // 0,1 pinned + current=2 -> refuse
    assert(small.acquire()->a == 3 && pin1->a == 1 && pin2->a == 2);  // old snapshots stay valid
  }

  // 2. Run gate: after close_and_drain succeeds nothing is running and nothing can enter.
  r2d::RunGate gate;
  gate.open();
  std::atomic<bool> go{true};
  std::atomic<long> in_flight{0}, entered{0}, violations{0};
  std::vector<std::thread> workers;
  for (int t = 0; t < 4; ++t)
    workers.emplace_back([&] {
      while (go.load()) {
        r2d::RunGate::Scope run(gate);
        if (!run) continue;
        ++in_flight; ++entered;
        std::this_thread::yield();
        --in_flight;
      }
    });
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  const bool drained = gate.close_and_drain(std::chrono::seconds(2));
  const long at_drain = in_flight.load();
  const long before = entered.load();
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  go = false;
  for (auto & w : workers) w.join();
  std::printf("gate: drained=%d in_flight_at_drain=%ld entered_after_close=%ld\n", drained, at_drain, entered.load() - before);
  assert(drained && at_drain == 0 && entered.load() == before);

  // 3. Drain timeout is reported, not hung.
  r2d::RunGate g2;
  g2.open();
  r2d::RunGate::Scope held(g2);
  assert(!g2.close_and_drain(std::chrono::milliseconds(20)));
  std::puts("all primitive checks passed");
}
