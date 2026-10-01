// Timer and service pipelines sharing one state variable (one callback group), and parameter validation with a cross-parameter constraint.
#include "check.hpp"
#include "counter_node/counter_node_node.hpp"

int main() {
  auto node = std::make_shared<demo::CounterNode>();
  auto & counts = node->sent<std_msgs::msg::Int64>("/demo/count");
  node->fire_timers();                    // step 1, limit 10: 0 -> 1
  node->fire_timers();
  CHECK(counts.size() == 2 && counts[0].data == 1 && counts[1].data == 2);
  auto res = node->call<std_srvs::srv::Trigger>("/demo/reset", {});
  CHECK(res.success && res.message == "counter reset");
  node->fire_timers();                    // the service reset the shared state
  CHECK(counts[2].data == 1);
  // the constraint `step <= limit` is checked on the proposed set as a whole
  auto r = node->set_parameters({rclcpp::Parameter("step", std::int64_t{20})});
  CHECK(!r.successful && r.reason == "step must not exceed limit");
  r = node->set_parameters({rclcpp::Parameter("step", std::int64_t{5}), rclcpp::Parameter("limit", std::int64_t{6})});
  CHECK(r.successful);
  node->fire_timers();                    // 1 + 5 = 6 >= limit 6 -> wraps to 0
  CHECK(counts[3].data == 0);
  r = node->set_parameters({rclcpp::Parameter("step", std::int64_t{0})});   // validation min = 1
  CHECK(!r.successful);
  std::puts("ok");
  return 0;
}
