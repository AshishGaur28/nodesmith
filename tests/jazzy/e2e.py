#!/usr/bin/env python3
"""End-to-end behaviour of the generated nodes on a real ROS 2 Jazzy, with rclpy. Run by the `jazzy` CI job after `colcon build`.

It mirrors tests/cpp/drivers/*.cpp, which run the same scenarios against the rclcpp stand-in. Usage: e2e.py <example> ...
"""

import subprocess
import sys
import time
from pathlib import Path

import rclpy
from rcl_interfaces.msg import Parameter as ParameterMsg
from rcl_interfaces.msg import ParameterType, ParameterValue
from rcl_interfaces.srv import SetParameters
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy

TIMEOUT = 30.0


class Harness(Node):
    def __init__(self):
        super().__init__("e2e_harness")
        self.received = {}

    def listen(self, msg_type, topic, reliability=ReliabilityPolicy.RELIABLE):
        self.received[topic] = []
        self.create_subscription(
            msg_type,
            topic,
            lambda m, t=topic: self.received[t].append(m),
            QoSProfile(depth=50, reliability=reliability),
        )

    def spin_until(self, condition, what, timeout=TIMEOUT):
        end = time.time() + timeout
        while time.time() < end:
            rclpy.spin_once(self, timeout_sec=0.05)
            if condition():
                return
        raise AssertionError(f"timed out waiting for {what}")

    def wait_for_subscribers(self, publisher):
        self.spin_until(
            lambda: publisher.get_subscription_count() > 0,
            f"a subscriber on {publisher.topic_name}",
        )

    def call(self, srv_type, name, request):
        client = self.create_client(srv_type, name)
        assert client.wait_for_service(timeout_sec=TIMEOUT), f"service {name} not available"
        future = client.call_async(request)
        rclpy.spin_until_future_complete(self, future, timeout_sec=TIMEOUT)
        assert future.done(), f"no answer from {name}"
        return future.result()

    def set_parameter(self, node_name, name, value):
        p = ParameterMsg(name=name)
        if isinstance(value, float):
            p.value = ParameterValue(type=ParameterType.PARAMETER_DOUBLE, double_value=value)
        else:
            p.value = ParameterValue(type=ParameterType.PARAMETER_INTEGER, integer_value=int(value))
        return self.call(
            SetParameters, f"{node_name}/set_parameters", SetParameters.Request(parameters=[p])
        ).results[0]


def start(package):
    return subprocess.Popen(["ros2", "run", package, package])


def scenario_02(h):
    from sensor_msgs.msg import Imu
    from std_msgs.msg import String

    h.listen(Imu, "/sensor/imu/filtered")
    h.listen(String, "/sensor/imu/status")
    pub = h.create_publisher(
        Imu, "/sensor/imu/raw", QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
    )
    h.wait_for_subscribers(pub)
    msg = Imu()
    msg.header.frame_id = "base"
    msg.linear_acceleration.z = 10.0
    pub.publish(msg)
    h.spin_until(lambda: h.received["/sensor/imu/filtered"], "the first filtered message")
    out = h.received["/sensor/imu/filtered"][0]
    assert abs(out.linear_acceleration.z - 8.5) < 1e-9 and out.header.frame_id == "base"
    assert (
        h.received["/sensor/imu/status"][0].data == "OK"
        if h.received["/sensor/imu/status"]
        else True
    )
    bad = h.set_parameter("/sensors/chassis/imu_filter_node", "alpha", 2.0)
    assert not bad.successful, "alpha above 1 must be rejected"
    good = h.set_parameter("/sensors/chassis/imu_filter_node", "alpha", 0.5)
    assert good.successful, good.reason


def scenario_03(h):
    from std_msgs.msg import Int64
    from std_srvs.srv import Trigger

    h.listen(Int64, "/demo/count")
    h.spin_until(lambda: len(h.received["/demo/count"]) >= 2, "two counter ticks")
    reply = h.call(Trigger, "/demo/reset", Trigger.Request())
    assert reply.success and reply.message == "counter reset"
    seen = len(h.received["/demo/count"])
    h.spin_until(lambda: len(h.received["/demo/count"]) > seen, "a tick after the reset")
    assert h.received["/demo/count"][seen].data in (1, 2), "the reset must restart the count"


def scenario_07(h):
    from my_interfaces.srv import GetGain, SetGain
    from std_srvs.srv import Trigger

    accepted = h.call(SetGain, "/processing/set_gain", SetGain.Request(gain=2.5))
    assert accepted.accepted and accepted.applied == 2.5
    refused = h.call(SetGain, "/processing/set_gain", SetGain.Request(gain=99.0))
    assert not refused.accepted and refused.applied == 2.5
    assert h.call(GetGain, "/processing/get_gain", GetGain.Request()).gain == 2.5
    assert h.call(Trigger, "/processing/reset_gain", Trigger.Request()).success
    assert h.call(GetGain, "/processing/get_gain", GetGain.Request()).gain == 1.0


def scenario_08(h):
    from std_msgs.msg import Bool, Empty, Float64

    h.listen(Bool, "/monitoring/link_alive")
    h.listen(Float64, "/monitoring/heartbeat_age")
    h.spin_until(lambda: h.received["/monitoring/link_alive"], "the first link report")
    assert h.received["/monitoring/link_alive"][0].data is True
    assert h.set_parameter("/monitoring/link_watchdog_node", "timeout_s", 0.1).successful
    h.spin_until(
        lambda: (
            h.received["/monitoring/link_alive"]
            and h.received["/monitoring/link_alive"][-1].data is False
        ),
        "the link to be reported down",
        10,
    )
    # The timer checks every 200 ms, so with a 0.1 s timeout one heartbeat is seen as "up" only if the check happens within
    # 0.1 s of it. Widen the timeout first so that the check always falls inside it.
    assert h.set_parameter("/monitoring/link_watchdog_node", "timeout_s", 1.0).successful
    pub = h.create_publisher(Empty, "/monitoring/heartbeat", 10)
    h.wait_for_subscribers(pub)
    n = len(h.received["/monitoring/link_alive"])
    pub.publish(Empty())
    h.spin_until(
        lambda: any(m.data for m in h.received["/monitoring/link_alive"][n:]),
        "the link to be reported up again",
        10,
    )


def scenario_09(h):
    from std_msgs.msg import Float64, String

    h.listen(Float64, "/planning/speed")
    h.listen(String, "/planning/label")
    pub = h.create_publisher(Float64, "/planning/range", 10)
    h.wait_for_subscribers(pub)
    pub.publish(Float64(data=4.0))
    h.spin_until(lambda: h.received["/planning/speed"], "the first planned speed")
    assert abs(h.received["/planning/speed"][0].data - 1.0) < 1e-9
    h.spin_until(lambda: h.received["/planning/label"], "the first label")
    assert h.received["/planning/label"][0].data == "fast"
    pub.publish(Float64(data=-1.0))  # the user's function raises a fault: nothing is published
    pub.publish(Float64(data=4.0))
    h.spin_until(lambda: len(h.received["/planning/speed"]) >= 2, "the next planned speed")
    assert abs(h.received["/planning/speed"][1].data - 1.5) < 1e-9  # the fault left the state alone


SCENARIOS = {
    "02_filter_pipeline": scenario_02,
    "03_state_machine": scenario_03,
    "07_gain_service": scenario_07,
    "08_link_watchdog": scenario_08,
    "09_user_functions": scenario_09,
}


def packages():
    """example -> the package to run, from tests/jazzy/examples.txt (the one list of what is built and run)."""
    table = Path(__file__).with_name("examples.txt")
    rows = [
        line.split()
        for line in table.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return {row[0]: (row[4] if len(row) > 4 and row[4] != "-" else row[1]) for row in rows}


def main(examples):
    rclpy.init()
    failures = 0
    names = packages()
    for example in examples:
        scenario = SCENARIOS.get(example)
        if (
            scenario is None
        ):  # built (for example the plain pub/sub echo node) but has no behaviour check yet
            print(f"SKIP {example}: no scenario")
            continue
        proc = start(names[example])
        h = Harness()
        try:
            time.sleep(2.0)
            scenario(h)
            print(f"PASS {example}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"FAIL {example}: {e!r}")
        finally:
            h.destroy_node()
            proc.terminate()
            proc.wait(timeout=10)
    rclpy.shutdown()
    return failures


if __name__ == "__main__":
    sys.exit(1 if main(sys.argv[1:] or sorted(SCENARIOS)) else 0)
