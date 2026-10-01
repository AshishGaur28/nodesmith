// Test stub of rcl_interfaces/msg/SetParametersResult and ParameterDescriptor: only the members the generated node uses.
#pragma once
#include <cstdint>
#include <string>
#include <vector>
namespace rcl_interfaces::msg {
struct SetParametersResult { bool successful{false}; std::string reason; };
struct FloatingPointRange { double from_value{0}, to_value{0}, step{0}; };
struct IntegerRange { std::int64_t from_value{0}, to_value{0}, step{0}; };
struct ParameterDescriptor {
  std::string description;
  bool read_only{false};
  std::vector<FloatingPointRange> floating_point_range;
  std::vector<IntegerRange> integer_range;
};
}  // namespace rcl_interfaces::msg
