// Reference implementation of the SPEC-12 runtime primitives. Generated nodes ship an
// equivalent header; this one is the normative behaviour and is stress-tested
// (concurrency_primitives_test.cpp, run under ThreadSanitizer).
#pragma once
#include <array>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <thread>
#include <utility>

namespace r2d {

// tag::snapshot_ring[]
// Immutable-after-publication parameter snapshots (SPEC-12 §5).
// Readers: lock-free, allocation-free. Writer: one at a time (rcl serialises set-parameters).
// N must be >= (max concurrent readers) + 2 so a free slot always exists.
template <typename T, std::size_t N>
class SnapshotRing {
  static_assert(N >= 3, "need at least current + one free + one pinned slot");

  struct Slot {
    T value{};
    std::atomic<std::uint32_t> readers{0};
  };

 public:
  explicit SnapshotRing(const T & initial) { slots_[0].value = initial; }

  class Guard {  // pins one snapshot for the lifetime of an execution
   public:
    Guard(Guard && o) noexcept : slot_(std::exchange(o.slot_, nullptr)) {}
    Guard(const Guard &) = delete;
    Guard & operator=(const Guard &) = delete;
    ~Guard() { if (slot_) slot_->readers.fetch_sub(1); }
    const T & operator*() const { return slot_->value; }
    const T * operator->() const { return &slot_->value; }

   private:
    friend class SnapshotRing;
    explicit Guard(Slot * s) : slot_(s) {}
    Slot * slot_;
  };

  Guard acquire() noexcept {  // seq_cst throughout: the reader/writer handshake is a Dekker pattern
    for (;;) {
      const std::size_t i = current_.load();
      slots_[i].readers.fetch_add(1);
      if (current_.load() == i) return Guard(&slots_[i]);  // still current: contents are stable
      slots_[i].readers.fetch_sub(1);                      // raced with a publish: retry
    }
  }

  // Copies the current snapshot into a free slot, lets `edit` modify it, then swaps atomically.
  // Returns false (nothing changed) if every other slot is still pinned by a reader.
  template <typename Edit>
  bool publish(Edit && edit) {
    const std::size_t cur = current_.load();
    for (std::size_t k = 1; k < N; ++k) {
      const std::size_t i = (cur + k) % N;
      if (slots_[i].readers.load() == 0) {
        slots_[i].value = slots_[cur].value;
        edit(slots_[i].value);
        current_.store(i);
        return true;
      }
    }
    return false;
  }

 private:
  std::array<Slot, N> slots_;
  std::atomic<std::size_t> current_{0};
};
// end::snapshot_ring[]

// tag::run_gate[]
// Run gate (SPEC-12 §6): executions enter only while open; deactivate closes and drains.
class RunGate {
 public:
  class Scope {
   public:
    explicit Scope(RunGate & g) noexcept : gate_(g), entered_(g.try_enter()) {}
    ~Scope() { if (entered_) gate_.leave(); }
    Scope(const Scope &) = delete;
    Scope & operator=(const Scope &) = delete;
    explicit operator bool() const noexcept { return entered_; }

   private:
    RunGate & gate_;
    bool entered_;
  };

  void open() noexcept { word_.fetch_or(kOpen); }

  // Returns true when no execution is in flight before `timeout` expires (else the gate
  // stays closed and the caller reports ERR_LCY_102).
  bool close_and_drain(std::chrono::nanoseconds timeout) noexcept {
    word_.fetch_and(~kOpen);
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while ((word_.load() & ~kOpen) != 0) {
      if (std::chrono::steady_clock::now() >= deadline) return false;
      std::this_thread::yield();
    }
    return true;
  }

 private:
  static constexpr std::uint64_t kOpen = 1ULL << 63;
  bool try_enter() noexcept {
    std::uint64_t w = word_.load();
    do {
      if (!(w & kOpen)) return false;
    } while (!word_.compare_exchange_weak(w, w + 1));
    return true;
  }
  void leave() noexcept { word_.fetch_sub(1); }
  std::atomic<std::uint64_t> word_{0};
};
// end::run_gate[]

}  // namespace r2d
