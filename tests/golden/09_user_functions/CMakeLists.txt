cmake_minimum_required(VERSION 3.16)
project(speed_planner_node)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
endif()

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  # No fused multiply-add: float results must be bit-identical on every target (SPEC-02 §6).
  add_compile_options(-Wall -Wextra -ffp-contract=off)
endif()

find_package(ament_cmake REQUIRED)
find_package(diagnostic_msgs REQUIRED)
find_package(rcl_interfaces REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_components REQUIRED)
find_package(std_msgs REQUIRED)

# The node is a shared library registered as a ROS 2 component, so it can be loaded into a
# component container (`ros2 component load`) as well as run on its own by the executable below.
set(COMPONENT_SOURCES
  src/speed_planner_node_node.cpp
  src/interfaces.cpp
  src/parameters.cpp
  src/diagnostics.cpp)

# The functions the manifest declares are written by the user. logic/ is a copy of their
# folder (see include/speed_planner_node/logic_api.hpp) and src/logic_defaults.cpp holds weak
# placeholders for whatever they have not written.
file(GLOB_RECURSE USER_LOGIC CONFIGURE_DEPENDS logic/*.cpp logic/*.cc logic/*.cxx)

add_library(speed_planner_node_component SHARED
  ${COMPONENT_SOURCES}
  src/logic_defaults.cpp
  ${USER_LOGIC})
target_include_directories(speed_planner_node_component PRIVATE logic)
target_include_directories(speed_planner_node_component PUBLIC include)
ament_target_dependencies(speed_planner_node_component
  diagnostic_msgs
  rcl_interfaces
  rclcpp
  rclcpp_components
  std_msgs
  )
rclcpp_components_register_nodes(speed_planner_node_component "planning::SpeedPlannerNode")

add_executable(speed_planner_node src/main.cpp src/app.cpp)
target_link_libraries(speed_planner_node speed_planner_node_component)

install(
  TARGETS speed_planner_node_component
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION bin)
install(TARGETS speed_planner_node DESTINATION lib/${PROJECT_NAME})
install(DIRECTORY launch config DESTINATION share/${PROJECT_NAME})

if(BUILD_TESTING)
  # The ROS 2 linters run under `colcon test` when they are installed. The copyright and cpplint
  # checks are skipped: they want a license header in every file and Google header guards.
  find_package(ament_lint_auto QUIET)
  if(ament_lint_auto_FOUND)
    set(ament_cmake_copyright_FOUND TRUE)
    set(ament_cmake_cpplint_FOUND TRUE)
    ament_lint_auto_find_test_dependencies()
  endif()
endif()

ament_package()
