// The generated code's only contact with the version-sensitive parts of the ROS 2 client library
// (rclcpp). Everything that has changed, or could change, between ROS 2 releases is called
// through the `binding` namespace below; the rest of the package is the same for every release.
//
// This file is the implementation for ROS 2 Jazzy. To use another release, replace this file (or
// add a branch on RCLCPP_VERSION_MAJOR from <rclcpp/version.h>) and nothing else: the engine, the
// adapters, `main()` and your `logic/` stay as they are.
#pragma once

#include <chrono>
#include <cstddef>
#include <functional>
#include <memory>
#include <string>
#include <utility>

#include <rclcpp/rclcpp.hpp>

namespace binding {

// ---- process ----------------------------------------------------------------------------------

inline void init(int argc, char ** argv) {
  rclcpp::init(argc, argv);
}

inline void shutdown() {
  rclcpp::shutdown();
}

// ---- executors --------------------------------------------------------------------------------

inline std::unique_ptr<rclcpp::Executor> make_single_threaded_executor() {
  return std::make_unique<rclcpp::executors::SingleThreadedExecutor>();
}

inline std::unique_ptr<rclcpp::Executor> make_multi_threaded_executor(std::size_t threads) {
  return std::make_unique<rclcpp::executors::MultiThreadedExecutor>(
      rclcpp::ExecutorOptions(), threads);
}

// ---- node resources ---------------------------------------------------------------------------

template <class Message, class Callback>
typename rclcpp::Subscription<Message>::SharedPtr create_subscription(
    rclcpp::Node & node, const std::string & topic, const rclcpp::QoS & qos, Callback && callback,
    rclcpp::CallbackGroup::SharedPtr group) {
  rclcpp::SubscriptionOptions options;
  options.callback_group = group;
  return node.create_subscription<Message>(topic, qos, std::forward<Callback>(callback), options);
}

template <class Service, class Callback>
typename rclcpp::Service<Service>::SharedPtr create_service(
    rclcpp::Node & node, const std::string & name, Callback && callback,
    rclcpp::CallbackGroup::SharedPtr group) {
  return node.create_service<Service>(
      name, std::forward<Callback>(callback), rclcpp::ServicesQoS(), group);
}

template <class Callback>
rclcpp::TimerBase::SharedPtr create_timer(
    rclcpp::Node & node, rclcpp::Clock::SharedPtr clock, std::chrono::milliseconds period,
    Callback && callback, rclcpp::CallbackGroup::SharedPtr group) {
  return rclcpp::create_timer(
      &node, clock, rclcpp::Duration(period), std::forward<Callback>(callback), group);
}

// ---- parameters -------------------------------------------------------------------------------

using ParameterCallbackHandle = rclcpp::Node::OnSetParametersCallbackHandle::SharedPtr;

template <class Callback>
ParameterCallbackHandle add_parameter_callback(rclcpp::Node & node, Callback && callback) {
  return node.add_on_set_parameters_callback(std::forward<Callback>(callback));
}

}  // namespace binding
