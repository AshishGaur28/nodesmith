cmake_minimum_required(VERSION 3.16)
project(speed_planner_node)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
endif()
if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -ffp-contract=off)  # no fused multiply-add: float results must be bit-identical on every target (SPEC-02 §6)
endif()

find_package(ament_cmake REQUIRED)
find_package(diagnostic_msgs REQUIRED)
find_package(rcl_interfaces REQUIRED)
find_package(rclcpp REQUIRED)
find_package(std_msgs REQUIRED)

# The functions the manifest declares are written by the user: logic/ is a copy of their folder (see include/speed_planner_node/logic_api.hpp),
# and src/logic_defaults.cpp holds weak placeholders for whatever they have not written.
file(GLOB_RECURSE USER_LOGIC CONFIGURE_DEPENDS logic/*.cpp logic/*.cc logic/*.cxx)
add_executable(speed_planner_node src/main.cpp src/speed_planner_node_node.cpp src/logic_defaults.cpp ${USER_LOGIC})
target_include_directories(speed_planner_node PRIVATE logic)
target_include_directories(speed_planner_node PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)
ament_target_dependencies(speed_planner_node diagnostic_msgs rcl_interfaces rclcpp std_msgs)

install(TARGETS speed_planner_node DESTINATION lib/${PROJECT_NAME})
install(DIRECTORY include/ DESTINATION include)
install(DIRECTORY launch config DESTINATION share/${PROJECT_NAME})

ament_package()
