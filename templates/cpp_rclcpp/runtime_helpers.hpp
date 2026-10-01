// Numeric semantics (SPEC-02 §5, §6), diagnostics ring (SPEC-12 §7) and message-field helpers shared by every generated node.
// No ROS dependency: generated pipeline code only needs this header and the standard library.
#pragma once
#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <string>
#include <type_traits>
#include <vector>

namespace r2d {

// ---- runtime diagnostic codes (SPEC-04 §4.5) -------------------------------------------------------------------------
enum class Code : std::uint32_t { ERR_RUN_101 = 101, ERR_RUN_102 = 102, ERR_RUN_103 = 103 };

inline const char * code_name(Code c) {
  switch (c) {
    case Code::ERR_RUN_101: return "ERR_RUN_101";
    case Code::ERR_RUN_102: return "ERR_RUN_102";
    case Code::ERR_RUN_103: return "ERR_RUN_103";
  }
  return "ERR_UNKNOWN";
}
inline const char * code_message(Code c) {
  switch (c) {
    case Code::ERR_RUN_101: return "numeric fault: execution aborted, nothing committed or published";
    case Code::ERR_RUN_102: return "timer firing skipped: previous execution still running";
    case Code::ERR_RUN_103: return "parameter update rejected";
  }
  return "";
}

// ---- integers wrap in two's complement at their width; division faults (SPEC-02 §6) -------------------------------------
template <typename T> using U = std::make_unsigned_t<T>;
template <typename T> inline T wrap_add(T a, T b) { return static_cast<T>(static_cast<U<T>>(static_cast<U<T>>(a) + static_cast<U<T>>(b))); }
template <typename T> inline T wrap_sub(T a, T b) { return static_cast<T>(static_cast<U<T>>(static_cast<U<T>>(a) - static_cast<U<T>>(b))); }
template <typename T> inline T wrap_mul(T a, T b) { return static_cast<T>(static_cast<U<T>>(static_cast<U<T>>(a) * static_cast<U<T>>(b))); }
template <typename T> inline T wrap_neg(T a) { return static_cast<T>(static_cast<U<T>>(U<T>(0) - static_cast<U<T>>(a))); }
template <typename T> inline T iabs(T a) { return a < 0 ? wrap_neg<T>(a) : a; }  // abs(INT_MIN) wraps to INT_MIN

template <typename T> inline T idiv(T a, T b, bool & fault) {
  if (b == 0 || (b == T(-1) && a == std::numeric_limits<T>::min())) { fault = true; return 0; }
  return static_cast<T>(a / b);
}
template <typename T> inline T imod(T a, T b, bool & fault) {
  if (b == 0) { fault = true; return 0; }
  if (b == T(-1)) return 0;  // INT_MIN % -1 is 0, not a fault
  return static_cast<T>(a % b);
}

// ---- floats ------------------------------------------------------------------------------------------------------------
template <typename T> inline T min_(T a, T b) { return b < a ? b : a; }
template <typename T> inline T max_(T a, T b) { return a < b ? b : a; }
template <typename T> inline T clamp_(T v, T lo, T hi, bool & fault) {
  if (!(lo <= hi)) { fault = true; return v; }
  return min_<T>(max_<T>(v, lo), hi);
}
inline double fsqrt(double x, bool & fault) {
  if (x < 0) { fault = true; return 0.0; }
  return std::sqrt(x);
}
inline double low_pass(double curr, double prev, double alpha, bool & fault) {
  if (!(alpha >= 0.0 && alpha <= 1.0)) { fault = true; return prev; }
  return prev + alpha * (curr - prev);
}
inline double deadband(double x, double threshold) { return std::fabs(x) < threshold ? 0.0 : x; }
inline double rate_limit(double target, double prev, double rise_per_s, double fall_per_s, double dt) {
  const double up = rise_per_s * dt, down = fall_per_s * dt, d = target - prev;
  if (d > up) return prev + up;
  if (d < -down) return prev - down;
  return target;
}
template <typename C> inline std::int64_t len(const C & c) { return static_cast<std::int64_t>(c.size()); }
template <typename V, typename I> inline typename V::value_type at(const V & v, I i, bool & fault) {
  if (i < 0 || static_cast<std::uint64_t>(i) >= v.size()) { fault = true; return typename V::value_type{}; }
  return v[static_cast<std::size_t>(i)];
}

// ---- conversions ---------------------------------------------------------------------------------------------------------
template <typename T, typename S> inline T to_int(S x, bool & fault) {
  if constexpr (std::is_floating_point_v<S>) {
    // [-2^(bits-1), 2^(bits-1)) in doubles: both bounds are exactly representable
    constexpr double hi = static_cast<double>(std::uint64_t{1} << std::numeric_limits<T>::digits);
    constexpr double lo = -hi;
    if (!(static_cast<double>(x) >= lo && static_cast<double>(x) < hi)) { fault = true; return 0; }
    return static_cast<T>(x);  // truncates toward zero
  } else {
    if (x < static_cast<S>(std::numeric_limits<T>::min()) || x > static_cast<S>(std::numeric_limits<T>::max())) { fault = true; return 0; }
    return static_cast<T>(x);
  }
}
inline std::int64_t u64_to_i64(std::uint64_t v, bool & fault) {
  if (v > static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max())) { fault = true; return 0; }
  return static_cast<std::int64_t>(v);
}
template <typename T, typename C> inline std::vector<T> to_vector(const C & c) {
  std::vector<T> out;
  out.reserve(c.size());
  for (const auto & e : c) out.push_back(static_cast<T>(e));
  return out;
}
// builtin_interfaces Time/Duration <-> float64 seconds: sec + nanosec * 1e-9 (SPEC-02 §2.1)
template <typename Stamp> inline double time_to_sec(const Stamp & t) {
  return static_cast<double>(t.sec) + static_cast<double>(t.nanosec) * 1e-9;
}
template <typename Stamp> inline void set_time(Stamp & t, double seconds) {
  double whole = std::floor(seconds);
  auto nanos = static_cast<std::int64_t>(std::llround((seconds - whole) * 1e9));
  if (nanos >= 1000000000) { whole += 1.0; nanos -= 1000000000; }
  t.sec = static_cast<decltype(t.sec)>(whole);
  t.nanosec = static_cast<decltype(t.nanosec)>(nanos);
}
template <typename Dst, typename Src> inline void assign_array(Dst & dst, const Src & src) {
  dst.clear();
  for (const auto & e : src) dst.push_back(static_cast<typename Dst::value_type>(e));
}

// ---- sink checks: a NaN/Inf or out-of-range value reaching a state write or message field is a fault (SPEC-02 §6) ---------
template <typename F> inline void check_finite(F x, bool & fault) { if (!std::isfinite(x)) fault = true; }
template <typename V> inline void check_finite_all(const V & v, bool & fault) { for (auto x : v) if (!std::isfinite(x)) fault = true; }
template <typename T, typename S> inline void check_range(S v, bool & fault) {
  if constexpr (std::is_same_v<T, std::uint64_t>) { if (v < 0) fault = true; }
  else if (v < static_cast<S>(std::numeric_limits<T>::min()) || v > static_cast<S>(std::numeric_limits<T>::max())) fault = true;
}

// ---- diagnostics ring (SPEC-12 §7) ----------------------------------------------------------------------------------------
// Bounded, allocation-free, safe for any number of producers and one consumer (the drain timer). A full ring overwrites its
// oldest entries; `dropped()` counts them. Fields are atomics so an overwrite during a drain is a detected miss, never a data race.
template <std::size_t N = 64>
class DiagRing {
  static_assert((N & (N - 1)) == 0, "capacity must be a power of two");
  struct Slot {
    std::atomic<std::uint64_t> seq{0};
    std::atomic<std::uint32_t> code{0};
    std::atomic<const char *> who{nullptr};
  };

 public:
  void report(Code c, const char * who) noexcept {
    const std::uint64_t i = head_.fetch_add(1);
    Slot & s = slots_[i & (N - 1)];
    s.code.store(static_cast<std::uint32_t>(c));
    s.who.store(who);
    s.seq.store(i + 1);
    counts_[index(c)].fetch_add(1);
  }

  // Calls `emit(Code, const char * who)` for every entry not yet drained, oldest first. Consumer thread only.
  template <typename Emit>
  void drain(Emit && emit) {
    const std::uint64_t head = head_.load();
    if (head - tail_ > N) { dropped_ += head - tail_ - N; tail_ = head - N; }
    while (tail_ < head) {
      Slot & s = slots_[tail_ & (N - 1)];
      if (s.seq.load() < tail_ + 1) break;           // a producer has reserved the slot but not finished: next drain
      const auto code = s.code.load();
      const char * who = s.who.load();
      if (s.seq.load() != tail_ + 1) { ++dropped_; ++tail_; continue; }  // overwritten while reading
      emit(static_cast<Code>(code), who);
      ++tail_;
    }
  }

  std::uint64_t dropped() const noexcept { return dropped_; }
  std::uint64_t count(Code c) const noexcept { return counts_[index(c)].load(); }

 private:
  static std::size_t index(Code c) { return static_cast<std::size_t>(static_cast<std::uint32_t>(c) - 101); }
  std::array<Slot, N> slots_;
  std::atomic<std::uint64_t> head_{0};
  std::uint64_t tail_{0};
  std::uint64_t dropped_{0};
  std::array<std::atomic<std::uint64_t>, 3> counts_{};
};

}  // namespace r2d
