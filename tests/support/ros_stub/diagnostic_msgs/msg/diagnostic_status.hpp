#pragma once
#include <cstdint>
#include <string>
#include <vector>
#include "diagnostic_msgs/msg/key_value.hpp"
namespace diagnostic_msgs::msg {
struct DiagnosticStatus {
  static constexpr std::uint8_t OK = 0, WARN = 1, ERROR = 2, STALE = 3;
  std::uint8_t level{0};
  std::string name, message, hardware_id;
  std::vector<KeyValue> values;
};
}  // namespace diagnostic_msgs::msg
