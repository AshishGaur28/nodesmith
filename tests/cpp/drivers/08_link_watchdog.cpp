// A startup pipeline, a subscriber pipeline and a timer pipeline sharing `last_seen`, on the steady clock.
#include <chrono>
#include <thread>
#include "check.hpp"
#include "link_watchdog_node/link_watchdog_node_node.hpp"

int main() {
  auto node = std::make_shared<monitoring::LinkWatchdogNode>();
  auto & alive = node->sent<std_msgs::msg::Bool>("/monitoring/link_alive");
  auto & age = node->sent<std_msgs::msg::Float64>("/monitoring/heartbeat_age");
  node->fire_timers();                   // the startup pipeline stamped last_seen, so the link is alive
  CHECK(alive.size() == 1 && alive[0].data && age[0].data >= 0.0 && age[0].data < 1.0);

  CHECK(node->set_parameters({rclcpp::Parameter("timeout_s", 0.1)}).successful);
  std::this_thread::sleep_for(std::chrono::milliseconds(200));
  node->fire_timers();                   // no heartbeat for 0.2 s against a 0.1 s timeout
  CHECK(!alive[1].data && age[1].data >= 0.15);

  node->deliver("/monitoring/heartbeat", std_msgs::msg::Empty{});   // a heartbeat resets the age
  node->fire_timers();
  CHECK(alive[2].data && age[2].data < 0.1);
  CHECK(!node->set_parameters({rclcpp::Parameter("timeout_s", 0.01)}).successful);   // validation min = 0.1
  std::puts("ok");
  return 0;
}
