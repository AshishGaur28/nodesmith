#pragma once
#include <vector>
#include "diagnostic_msgs/msg/diagnostic_status.hpp"
namespace builtin_interfaces::msg {
struct Time { std::int32_t sec{0}; std::uint32_t nanosec{0}; };
struct Duration { std::int32_t sec{0}; std::uint32_t nanosec{0}; };
}
namespace diagnostic_msgs::msg {
struct DiagnosticArray {
  struct Header { builtin_interfaces::msg::Time stamp; std::string frame_id; } header;
  std::vector<DiagnosticStatus> status;
};
}  // namespace diagnostic_msgs::msg
