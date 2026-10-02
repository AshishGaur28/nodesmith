"""Named constants for everything the manifest names: topics, services, QoS depths, timer
periods, parameter names, pipeline names and the node itself (``interface_names.hpp``).

The generated code never repeats such a literal at a call site. The functions here give the
constant's name as the other emitters spell it (``names::kPubTopicFilteredImuPub``) and
``constants`` lists the definitions that ``interface_names.hpp`` holds, so the two cannot disagree.
"""

from .literals import cpp_string
from .naming import camel

DIAGNOSTICS_TOPIC = "/diagnostics"
DIAGNOSTICS_DEPTH = 10
DIAGNOSTICS_PERIOD_S = 1


def _const(prefix: str, identifier: str) -> str:
    return f"names::k{prefix}{camel(identifier)}"


def pub_topic(identifier: str) -> str:
    """Constant for a publisher's topic."""
    return _const("PubTopic", identifier)


def sub_topic(identifier: str) -> str:
    """Constant for a subscriber's topic."""
    return _const("SubTopic", identifier)


def pub_depth(identifier: str) -> str:
    """Constant for a publisher's QoS depth."""
    return _const("PubQosDepth", identifier)


def sub_depth(identifier: str) -> str:
    """Constant for a subscriber's QoS depth."""
    return _const("SubQosDepth", identifier)


def service_name(identifier: str) -> str:
    """Constant for a service's name."""
    return _const("ServiceName", identifier)


def period_ms(pipeline: str) -> str:
    """Constant for a timer pipeline's period in milliseconds."""
    return _const("PeriodMs", pipeline)


def parameter(name: str) -> str:
    """Constant for a parameter's name."""
    return _const("Param", name)


def pipeline(name: str) -> str:
    """Constant for a pipeline's name."""
    return _const("Pipeline", name)


def _bare(qualified: str) -> str:
    return qualified.removeprefix("names::")


def constants(ir: dict) -> list[str]:
    """The ``inline constexpr`` definitions of ``interface_names.hpp``, in a fixed order."""
    meta = ir["node_meta"]
    interfaces = ir["interfaces"]
    lines = [
        f"inline constexpr char kNodeName[] = {cpp_string(meta['name'])};",
        f"inline constexpr char kNodeNamespace[] = {cpp_string(meta['namespace'])};",
        f"inline constexpr char kDiagnosticsTopic[] = {cpp_string(DIAGNOSTICS_TOPIC)};",
        f"inline constexpr std::size_t kDiagnosticsQosDepth = {DIAGNOSTICS_DEPTH};",
        f"inline constexpr std::int64_t kDiagnosticsPeriodS = {DIAGNOSTICS_PERIOD_S};",
        f"inline constexpr char kParametersWho[] = {cpp_string('parameters')};",
        f"inline constexpr std::size_t kExecutorThreads = {ir['concurrency']['threads']};",
    ]
    for kind, topic_name, depth_name in (
        ("publishers", pub_topic, pub_depth),
        ("subscribers", sub_topic, sub_depth),
    ):
        for endpoint in interfaces[kind]:
            identifier = endpoint["identifier"]
            lines.append(
                f"inline constexpr char {_bare(topic_name(identifier))}[] = "
                f"{cpp_string(endpoint['topic'])};"
            )
            if endpoint["qos"]["history"] != "keep_all":
                lines.append(
                    f"inline constexpr std::size_t {_bare(depth_name(identifier))} = "
                    f"{endpoint['qos']['depth']};"
                )
    for service in interfaces["services"]:
        lines.append(
            f"inline constexpr char {_bare(service_name(service['identifier']))}[] = "
            f"{cpp_string(service['service_name'])};"
        )
    for dag in ir["execution_dags"]:
        lines.append(
            f"inline constexpr char {_bare(pipeline(dag['id']))}[] = {cpp_string(dag['id'])};"
        )
        if dag["trigger"]["kind"] == "timer":
            lines.append(
                f"inline constexpr std::int64_t {_bare(period_ms(dag['id']))} = "
                f"{dag['trigger']['period_ms']};"
            )
    for entry in ir["parameters"]:
        lines.append(
            f"inline constexpr char {_bare(parameter(entry['name']))}[] = "
            f"{cpp_string(entry['name'])};"
        )
    return lines
