// Semantics of the numeric helpers and the diagnostics ring. Built with -fsanitize=thread in CI; also run locally.
#include <cassert>
#include <cstdio>
#include <limits>
#include <thread>
#include <vector>
#include "runtime.hpp"

using namespace r2d;

int main() {
  bool f = false;
  // integers wrap at their width
  assert(wrap_add<std::int32_t>(std::numeric_limits<std::int32_t>::max(), 1) == std::numeric_limits<std::int32_t>::min());
  assert(wrap_sub<std::int64_t>(std::numeric_limits<std::int64_t>::min(), 1) == std::numeric_limits<std::int64_t>::max());
  assert(wrap_mul<std::int32_t>(65536, 65536) == 0);
  assert(wrap_neg<std::int32_t>(std::numeric_limits<std::int32_t>::min()) == std::numeric_limits<std::int32_t>::min());
  assert(iabs<std::int32_t>(-5) == 5);
  // division truncates toward zero, % takes the dividend's sign, faults on zero and INT_MIN / -1
  assert(idiv<std::int32_t>(-7, 2, f) == -3 && !f);
  assert(imod<std::int32_t>(-7, 2, f) == -1 && !f);
  assert(imod<std::int32_t>(std::numeric_limits<std::int32_t>::min(), -1, f) == 0 && !f);
  idiv<std::int32_t>(1, 0, f); assert(f); f = false;
  imod<std::int64_t>(1, 0, f); assert(f); f = false;
  idiv<std::int64_t>(std::numeric_limits<std::int64_t>::min(), -1, f); assert(f); f = false;
  // built-ins
  assert(clamp_<double>(5.0, 0.0, 1.0, f) == 1.0 && !f);
  clamp_<double>(5.0, 2.0, 1.0, f); assert(f); f = false;
  fsqrt(-1.0, f); assert(f); f = false;
  low_pass(2.0, 1.0, 1.5, f); assert(f); f = false;
  assert(low_pass(2.0, 1.0, 0.5, f) == 1.5 && !f);
  assert(deadband(0.05, 0.1) == 0.0 && deadband(0.5, 0.1) == 0.5);
  assert(rate_limit(10.0, 0.0, 2.0, 3.0, 1.0) == 2.0 && rate_limit(-10.0, 0.0, 2.0, 3.0, 1.0) == -3.0 && rate_limit(1.0, 0.0, 2.0, 3.0, 1.0) == 1.0);
  assert(rate_limit(10.0, 0.0, 2.0, 3.0, 0.0) == 0.0);
  const std::vector<double> v{1.0, 2.0};
  assert(at(v, std::int32_t{1}, f) == 2.0 && !f);
  at(v, std::int64_t{2}, f); assert(f); f = false;
  at(v, std::int32_t{-1}, f); assert(f); f = false;
  // conversions
  assert(to_int<std::int32_t>(-2.9, f) == -2 && !f);
  to_int<std::int32_t>(3e9, f); assert(f); f = false;
  to_int<std::int64_t>(9.3e18, f); assert(f); f = false;
  assert(to_int<std::int64_t>(-9223372036854775808.0, f) == std::numeric_limits<std::int64_t>::min() && !f);
  to_int<std::int32_t>(std::int64_t{1} << 40, f); assert(f); f = false;
  to_int<std::int32_t>(std::nan(""), f); assert(f); f = false;
  u64_to_i64(std::numeric_limits<std::uint64_t>::max(), f); assert(f); f = false;
  struct Stamp { std::int32_t sec; std::uint32_t nanosec; } t{1, 500000000};
  assert(time_to_sec(t) == 1.5);
  set_time(t, -0.25); assert(t.sec == -1 && t.nanosec == 750000000);
  set_time(t, 2.9999999999); assert(t.sec == 3 && t.nanosec == 0);
  // sink checks
  check_finite(std::numeric_limits<double>::infinity(), f); assert(f); f = false;
  check_range<std::uint8_t>(std::int32_t{256}, f); assert(f); f = false;
  check_range<std::uint8_t>(std::int32_t{255}, f); assert(!f);
  check_range<std::uint64_t>(std::int64_t{-1}, f); assert(f); f = false;
  check_range<std::int8_t>(std::int32_t{-129}, f); assert(f); f = false;

  // DiagRing: one producer, in order
  {
    DiagRing<8> ring;
    ring.report(Code::ERR_RUN_101, "a"); ring.report(Code::ERR_RUN_103, "b");
    std::vector<std::uint32_t> seen;
    ring.drain([&](Code c, const char *) { seen.push_back(static_cast<std::uint32_t>(c)); });
    assert((seen == std::vector<std::uint32_t>{101, 103}) && ring.dropped() == 0);
    assert(ring.count(Code::ERR_RUN_101) == 1);
  }
  // a full ring drops the oldest and counts them
  {
    DiagRing<4> ring;
    for (int i = 0; i < 10; ++i) ring.report(Code::ERR_RUN_101, "x");
    int n = 0;
    ring.drain([&](Code, const char *) { ++n; });
    assert(n == 4 && ring.dropped() == 6);
  }
  // several producers and a drainer at once: no data race (ThreadSanitizer), nothing lost or invented
  {
    DiagRing<64> ring;
    std::atomic<bool> stop{false};
    std::atomic<std::uint64_t> drained{0};
    std::thread consumer([&] {
      while (!stop.load()) ring.drain([&](Code, const char *) { drained.fetch_add(1); });
      ring.drain([&](Code, const char *) { drained.fetch_add(1); });
    });
    std::vector<std::thread> producers;
    for (int p = 0; p < 4; ++p) producers.emplace_back([&] { for (int i = 0; i < 20000; ++i) ring.report(Code::ERR_RUN_102, "p"); });
    for (auto & t2 : producers) t2.join();
    stop.store(true);
    consumer.join();
    assert(ring.count(Code::ERR_RUN_102) == 80000);
    assert(drained.load() + ring.dropped() == 80000);
  }
  std::puts("runtime helper checks passed");
  return 0;
}
