// A small stand-in for rclcpp, for compiling and exercising the GENERATED node on a machine without ROS 2 (tests/support/).
// It implements only what generated nodes call, with the signatures of ROS 2 Jazzy as far as they are known; it proves the generated
// glue compiles against those signatures and behaves, not that real rclcpp accepts it. The Jazzy CI job is the real check.
#pragma once
#include <chrono>
#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <typeindex>
#include <vector>
#include "rcl_interfaces/msg/set_parameters_result.hpp"
#include "diagnostic_msgs/msg/diagnostic_array.hpp"

enum rcl_clock_type_t { RCL_CLOCK_UNINITIALIZED = 0, RCL_ROS_TIME = 1, RCL_SYSTEM_TIME = 2, RCL_STEADY_TIME = 3 };  // global, as in rcl/time.h

namespace rclcpp {

class Time { public: explicit Time(double s = 0) : s_(s) {} double seconds() const { return s_; } operator builtin_interfaces::msg::Time() const { return {}; } private: double s_; };
class Duration { public: template <class R, class P> Duration(std::chrono::duration<R, P> d) : ns_(std::chrono::duration_cast<std::chrono::nanoseconds>(d)) {} std::chrono::nanoseconds ns_; };
class Clock { public: using SharedPtr = std::shared_ptr<Clock>; explicit Clock(rcl_clock_type_t t = RCL_SYSTEM_TIME) : type_(t) {} Time now() const { return Time(now_seconds); } rcl_clock_type_t type_; double now_seconds{0}; };

enum class CallbackGroupType { MutuallyExclusive, Reentrant };
class CallbackGroup { public: using SharedPtr = std::shared_ptr<CallbackGroup>; explicit CallbackGroup(CallbackGroupType t) : type_(t) {} CallbackGroupType type_; };

struct KeepAll {};
class QoS {
 public:
  explicit QoS(std::size_t depth) : depth_(depth) {}
  explicit QoS(KeepAll) : keep_all_(true) {}
  QoS & reliable() { reliable_ = true; return *this; }
  QoS & best_effort() { reliable_ = false; return *this; }
  QoS & transient_local() { transient_ = true; return *this; }
  QoS & durability_volatile() { transient_ = false; return *this; }
  std::size_t depth_{10}; bool keep_all_{false}, reliable_{true}, transient_{false};
};
class ServicesQoS : public QoS { public: ServicesQoS() : QoS(10) {} };

struct NodeOptions {};
template <class M> struct SubscriptionOptionsT { CallbackGroup::SharedPtr callback_group; };
using SubscriptionOptions = SubscriptionOptionsT<void>;

class TimerBase { public: using SharedPtr = std::shared_ptr<TimerBase>; std::function<void()> callback; Duration period{std::chrono::milliseconds(0)}; CallbackGroup::SharedPtr group; };

template <class M> class Publisher {
 public:
  using SharedPtr = std::shared_ptr<Publisher<M>>;
  void publish(const M & m) { sent.push_back(m); }
  std::vector<M> sent;
};
template <class M> class Subscription { public: using SharedPtr = std::shared_ptr<Subscription<M>>; std::function<void(typename M::ConstSharedPtr)> callback; CallbackGroup::SharedPtr group; };
template <class S> class Service { public: using SharedPtr = std::shared_ptr<Service<S>>; std::function<void(std::shared_ptr<typename S::Request>, std::shared_ptr<typename S::Response>)> callback; CallbackGroup::SharedPtr group; };

class Parameter {
 public:
  Parameter(std::string n, bool v) : name_(std::move(n)), b_(v) {}
  Parameter(std::string n, std::int64_t v) : name_(std::move(n)), i_(v) {}
  Parameter(std::string n, double v) : name_(std::move(n)), d_(v) {}
  Parameter(std::string n, std::string v) : name_(std::move(n)), s_(std::move(v)) {}
  Parameter(std::string n, std::vector<double> v) : name_(std::move(n)), da_(std::move(v)) {}
  Parameter(std::string n, std::vector<std::int64_t> v) : name_(std::move(n)), ia_(std::move(v)) {}
  Parameter(std::string n, std::vector<bool> v) : name_(std::move(n)), ba_(std::move(v)) {}
  Parameter(std::string n, std::vector<std::string> v) : name_(std::move(n)), sa_(std::move(v)) {}
  Parameter(std::string n, std::vector<std::uint8_t> v) : name_(std::move(n)), bya_(std::move(v)) {}
  const std::string & get_name() const { return name_; }
  bool as_bool() const { return b_; }
  std::int64_t as_int() const { return i_; }
  double as_double() const { return d_; }
  const std::string & as_string() const { return s_; }
  const std::vector<double> & as_double_array() const { return da_; }
  const std::vector<std::int64_t> & as_integer_array() const { return ia_; }
  const std::vector<bool> & as_bool_array() const { return ba_; }
  const std::vector<std::string> & as_string_array() const { return sa_; }
  const std::vector<std::uint8_t> & as_byte_array() const { return bya_; }
 private:
  std::string name_; bool b_{}; std::int64_t i_{}; double d_{}; std::string s_; std::vector<double> da_; std::vector<std::int64_t> ia_; std::vector<bool> ba_;
  std::vector<std::string> sa_; std::vector<std::uint8_t> bya_;
};

namespace exceptions { struct InvalidParameterValueException : std::runtime_error { using std::runtime_error::runtime_error; }; }

struct OnSetParametersCallbackHandle {
  using SharedPtr = std::shared_ptr<OnSetParametersCallbackHandle>;
  std::function<rcl_interfaces::msg::SetParametersResult(const std::vector<Parameter> &)> callback;
};

class Node {
 public:
  using OnSetParametersCallbackHandle = rclcpp::OnSetParametersCallbackHandle;
  Node(const std::string & name, const std::string & ns, const NodeOptions & = NodeOptions()) : name_(name), ns_(ns) {}
  virtual ~Node() = default;

  template <class T> T declare_parameter(const std::string & name, const T & def, const rcl_interfaces::msg::ParameterDescriptor & = {}) {
    (void)name; return def;
  }
  OnSetParametersCallbackHandle::SharedPtr add_on_set_parameters_callback(std::function<rcl_interfaces::msg::SetParametersResult(const std::vector<Parameter> &)> cb) {
    handle_ = std::make_shared<OnSetParametersCallbackHandle>(); handle_->callback = std::move(cb); return handle_;
  }
  CallbackGroup::SharedPtr create_callback_group(CallbackGroupType t) { return std::make_shared<CallbackGroup>(t); }
  template <class M> typename Publisher<M>::SharedPtr create_publisher(const std::string & topic, const QoS &) {
    auto p = std::make_shared<Publisher<M>>(); publishers_[topic] = p; return p;
  }
  template <class M> typename Subscription<M>::SharedPtr create_subscription(const std::string & topic, const QoS &, std::function<void(typename M::ConstSharedPtr)> cb, const SubscriptionOptions & o = {}) {
    auto s = std::make_shared<Subscription<M>>(); s->callback = std::move(cb); s->group = o.callback_group; subscriptions_.emplace(topic, s); return s;
  }
  template <class S> typename Service<S>::SharedPtr create_service(const std::string & name, std::function<void(std::shared_ptr<typename S::Request>, std::shared_ptr<typename S::Response>)> cb, const QoS & = ServicesQoS(), CallbackGroup::SharedPtr g = nullptr) {
    auto s = std::make_shared<Service<S>>(); s->callback = std::move(cb); s->group = g; services_[name] = s; return s;
  }
  Clock::SharedPtr get_clock() { return clock_; }
  Time now() { return clock_->now(); }
  std::string get_fully_qualified_name() const { return (ns_ == "/" ? "" : ns_) + "/" + name_; }

  // -- test access -----------------------------------------------------------------------------------------------------
  template <class M> void deliver(const std::string & topic, const M & m, std::size_t which = 0) {
    auto range = subscriptions_.equal_range(topic);
    std::size_t k = 0;
    for (auto it = range.first; it != range.second; ++it, ++k)
      if (which == static_cast<std::size_t>(-1) || k == which) std::static_pointer_cast<Subscription<M>>(it->second)->callback(std::make_shared<const M>(m));
  }
  template <class M> std::vector<M> & sent(const std::string & topic) { return std::static_pointer_cast<Publisher<M>>(publishers_.at(topic))->sent; }
  template <class S> typename S::Response call(const std::string & name, const typename S::Request & req) {
    auto res = std::make_shared<typename S::Response>();
    std::static_pointer_cast<Service<S>>(services_.at(name))->callback(std::make_shared<typename S::Request>(req), res);
    return *res;
  }
  rcl_interfaces::msg::SetParametersResult set_parameters(const std::vector<Parameter> & p) { return handle_->callback(p); }
  void fire_timers() { for (auto & t : timers_) t->callback(); }
  std::vector<TimerBase::SharedPtr> timers_;
  Clock::SharedPtr clock_{std::make_shared<Clock>(RCL_ROS_TIME)};

 private:
  std::string name_, ns_;
  OnSetParametersCallbackHandle::SharedPtr handle_;
  std::map<std::string, std::shared_ptr<void>> publishers_, services_;
  std::multimap<std::string, std::shared_ptr<void>> subscriptions_;
};

template <class NodeT, class F>
TimerBase::SharedPtr create_timer(NodeT node, Clock::SharedPtr, Duration period, F && cb, CallbackGroup::SharedPtr group = nullptr) {
  auto t = std::make_shared<TimerBase>(); t->callback = std::forward<F>(cb); t->period = period; t->group = group;
  node->timers_.push_back(t);
  return t;
}

inline void init(int, char **) {}
inline void shutdown() {}
namespace executors {
struct SingleThreadedExecutor { template <class N> void add_node(N) {} void spin() {} };
struct ExecutorOptions {};
}  // namespace executors
using ExecutorOptions = executors::ExecutorOptions;
namespace executors { struct MultiThreadedExecutor { MultiThreadedExecutor(ExecutorOptions, std::size_t) {} template <class N> void add_node(N) {} void spin() {} }; }

}  // namespace rclcpp
