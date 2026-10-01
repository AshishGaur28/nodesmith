// Three services and a subscriber sharing the `gain` state: set/get/reset and scaling.
#include "check.hpp"
#include "gain_node/gain_node_node.hpp"

using SetGain = my_interfaces::srv::SetGain;
using GetGain = my_interfaces::srv::GetGain;

int main() {
  auto node = std::make_shared<processing::GainNode>();
  auto & out = node->sent<std_msgs::msg::Float64>("/processing/output");
  std_msgs::msg::Float64 in;
  in.data = 4.0;
  node->deliver("/processing/input", in);
  CHECK_NEAR(out[0].data, 4.0);                                // gain starts at 1.0

  SetGain::Request set;
  set.gain = 2.5;
  auto res = node->call<SetGain>("/processing/set_gain", set);
  CHECK(res.accepted && res.applied == 2.5);
  node->deliver("/processing/input", in);
  CHECK_NEAR(out[1].data, 10.0);

  set.gain = 99.0;                                              // above max_gain (10.0): refused, gain unchanged
  res = node->call<SetGain>("/processing/set_gain", set);
  CHECK(!res.accepted && res.applied == 2.5);
  set.gain = -1.0;
  CHECK(!node->call<SetGain>("/processing/set_gain", set).accepted);

  CHECK(node->call<GetGain>("/processing/get_gain", {}).gain == 2.5);
  auto reset = node->call<std_srvs::srv::Trigger>("/processing/reset_gain", {});
  CHECK(reset.success && reset.message == "gain reset");
  CHECK(node->call<GetGain>("/processing/get_gain", {}).gain == 1.0);
  std::puts("ok");
  return 0;
}
