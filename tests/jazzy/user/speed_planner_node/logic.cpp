// The user's implementation of the functions that examples/09_user_functions.toml declares: plain functions, nothing else.
// In a real project this is the `logic/` folder next to the manifest; the Jazzy check points `--logic-dir` here.
#include "speed_planner_node/logic_api.hpp"

namespace logic {

double plan_speed(double distance, double limit, double previous, bool & fault) {
  if (distance < 0) {
    fault = true;
    return 0.0;
  }
  const double wanted = distance * 0.5;
  const double target = wanted < limit ? wanted : limit;
  return previous + 0.5 * (target - previous);
}

std::string describe(double speed, bool & fault) {
  (void)fault;
  if (speed <= 0.0) return "stopped";
  return speed < 1.0 ? "slow" : "fast";
}

}  // namespace logic
