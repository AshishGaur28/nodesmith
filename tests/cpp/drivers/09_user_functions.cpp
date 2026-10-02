// The generated node calling functions the user implements: the call, a fault the user raises, and state kept by the pipeline.
#include "check.hpp"
#include "speed_planner_node/speed_planner_node_node.hpp"

int main() {
  // The user's functions (`logic::plan_speed`, `logic::describe`) are linked in; the node calls them directly.
  auto node = std::make_shared<planning::SpeedPlannerNode>();
  auto & speed = node->sent<std_msgs::msg::Float64>("/planning/speed");
  auto & label = node->sent<std_msgs::msg::String>("/planning/label");

  std_msgs::msg::Float64 in;
  in.data = 4.0;
  node->deliver("/planning/range", in);   // target min(4 * 0.5, limit 2.0) = 2.0; previous 0 -> 1.0
  CHECK(speed.size() == 1 && label.size() == 1);
  CHECK_NEAR(speed[0].data, 1.0);
  CHECK(label[0].data == "fast");
  node->deliver("/planning/range", in);   // previous is now 1.0 -> 1.5
  CHECK_NEAR(speed[1].data, 1.5);

  in.data = -1.0;                         // the user's function sets `fault`: nothing is committed or published
  node->deliver("/planning/range", in);
  CHECK(speed.size() == 2 && label.size() == 2);
  node->fire_timers();
  auto & diag = node->sent<diagnostic_msgs::msg::DiagnosticArray>("/diagnostics");
  CHECK(diag.size() == 1 && diag[0].status.size() == 1);
  CHECK(diag[0].status[0].values[0].value == "ERR_RUN_101" && diag[0].status[0].values[1].value == "plan");

  CHECK(node->set_parameters({rclcpp::Parameter("speed_limit", 0.5)}).successful);
  in.data = 4.0;
  node->deliver("/planning/range", in);   // the state was untouched by the fault (1.5); target is now 0.5 -> 1.0
  CHECK_NEAR(speed[2].data, 1.0);
  CHECK(label[2].data == "fast");
  std::puts("ok");
  return 0;
}
