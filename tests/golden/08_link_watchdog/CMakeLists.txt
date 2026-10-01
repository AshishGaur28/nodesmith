cmake_minimum_required(VERSION 3.16)
project(link_watchdog_node)

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

add_executable(link_watchdog_node src/main.cpp src/link_watchdog_node_node.cpp)
target_include_directories(link_watchdog_node PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)
ament_target_dependencies(link_watchdog_node diagnostic_msgs rcl_interfaces rclcpp std_msgs)

install(TARGETS link_watchdog_node DESTINATION lib/${PROJECT_NAME})
install(DIRECTORY include/ DESTINATION include)

ament_package()
