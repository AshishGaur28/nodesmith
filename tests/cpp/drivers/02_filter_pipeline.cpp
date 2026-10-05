// Behaviour of the generated imu_filter_node against the rclcpp stand-in: staged state, publication order, parameters, faults.
#include <limits>
#include "check.hpp"
#include "imu_filter_node/imu_filter_node_node.hpp"

using Imu = sensor_msgs::msg::Imu;
using Str = std_msgs::msg::String;

int main() {
  auto node = std::make_shared<sensors::chassis::ImuFilterNode>();
  auto & filtered = node->sent<Imu>("/sensor/imu/filtered");
  auto & status = node->sent<Str>("/sensor/imu/status");

  Imu in;
  in.header.frame_id = "base";
  in.linear_acceleration.z = 10.0;
  node->deliver("/sensor/imu/raw", in);
  // low_pass(10, prev 0, alpha 0.85) = 8.5: state is updated after the evaluation, the frame id is copied, the status is OK
  CHECK(filtered.size() == 1 && status.size() == 1);
  CHECK_NEAR(filtered[0].linear_acceleration.z, 8.5);
  CHECK(filtered[0].header.frame_id == "base");
  CHECK(status[0].data == "OK");
  node->deliver("/sensor/imu/raw", in);   // prev is now 8.5: 8.5 + 0.85 * (10 - 8.5) = 9.775
  CHECK_NEAR(filtered[1].linear_acceleration.z, 9.775);

  // a dynamic update is validated as a whole: alpha above 1 is rejected and nothing changes
  auto bad = node->set_parameters({rclcpp::Parameter("alpha", 2.0), rclcpp::Parameter("max_accel", 5.0)});
  CHECK(!bad.successful && bad.reason.find("'alpha'") != std::string::npos);
  auto ok = node->set_parameters({rclcpp::Parameter("alpha", 0.5), rclcpp::Parameter("max_accel", 5.0)});
  CHECK(ok.successful);
  node->deliver("/sensor/imu/raw", in);   // prev 9.775: 9.775 + 0.5 * (10 - 9.775) = 9.8875 > max_accel 5.0 -> OVERLIMIT
  CHECK_NEAR(filtered[2].linear_acceleration.z, 9.8875);
  CHECK(status[2].data == "OVERLIMIT");

  // a NaN reaching a sink aborts the execution: no publication, no state change, a diagnostic is raised
  Imu nan_in = in;
  nan_in.linear_acceleration.z = std::numeric_limits<double>::quiet_NaN();
  node->deliver("/sensor/imu/raw", nan_in);
  CHECK(filtered.size() == 3 && status.size() == 3);
  node->deliver("/sensor/imu/raw", in);   // prev is still 9.8875
  CHECK_NEAR(filtered[3].linear_acceleration.z, 9.8875 + 0.5 * (10 - 9.8875));
  node->fire_timers();                     // the diagnostics timer drains the ring
  auto & diag = node->sent<diagnostic_msgs::msg::DiagnosticArray>("/diagnostics");
  CHECK(diag.size() == 1 && diag[0].status.size() == 2);   // the rejected update, then the fault, oldest first
  CHECK(diag[0].status[0].values[0].value == "ERR_RUN_103");
  CHECK(diag[0].status[1].values[0].value == "ERR_RUN_101" && diag[0].status[1].values[1].value == "filter_imu");
  CHECK(diag[0].status[1].level == diagnostic_msgs::msg::DiagnosticStatus::ERROR && diag[0].status[0].level == diagnostic_msgs::msg::DiagnosticStatus::WARN);
  CHECK(diag[0].status[1].name == "/sensors/chassis/imu_filter_node");

  // The same events reach the ROS log, where an operator looks first.
  const auto has_log = [](const std::string & part) {
    for (const auto & line : rclcpp::log_lines()) {
      if (line.find(part) != std::string::npos) return true;
    }
    return false;
  };
  CHECK(has_log("[INFO] started"));
  CHECK(has_log("[WARN] parameter update rejected: 'alpha' out of range"));
  CHECK(has_log("[ERROR] ERR_RUN_101: numeric fault"));
  CHECK(has_log("pipeline 'filter_imu', 1 time(s)"));
  std::puts("ok");
  return 0;
}
