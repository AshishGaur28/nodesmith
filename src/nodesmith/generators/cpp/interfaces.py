"""The ROS interfaces adapter: publishers, subscriptions, services, timers and the code that
connects them to the engine (``interfaces.hpp``, ``interfaces.cpp``).

``InterfacesBuilder`` collects three lists that the templates paste in: ``members`` (fields of
the class), ``creates`` (statements of its constructor) and ``methods`` (declarations of the
``run_<pipeline>`` handlers). ``run_methods`` writes the handlers: each enters the engine's run
gate, asks the engine to evaluate the pipeline and publishes what the engine returns
(SPEC-02 section 7, step 5; SPEC-12 sections 3 to 6). Names and numbers come from
``interface_names.hpp`` (``names.py``), never from literals.
"""

from . import names
from .dag import Assignment, PipelineInfo
from .naming import ROS_CPP_TYPE, message_cpp

_CLOCK_MEMBER = {"steady": "clock_steady_", "system": "clock_system_", "ros": "node_.get_clock()"}


def qos_expression(qos: dict, depth: str) -> str:
    """The ``rclcpp::QoS`` built for an endpoint's QoS settings; ``depth`` names its constant."""
    if qos["history"] == "keep_all":
        text = "rclcpp::QoS(rclcpp::KeepAll())"
    else:
        text = f"rclcpp::QoS({depth})"
    text += ".reliable()" if qos["reliability"] == "reliable" else ".best_effort()"
    text += (
        ".transient_local()" if qos["durability"] == "transient_local" else ".durability_volatile()"
    )
    return text


class InterfacesBuilder:
    """Collects the members, creation statements and handler declarations of the adapter."""

    def __init__(self, ir: dict, pipelines: list[PipelineInfo]):
        self.ir = ir
        self.pipelines = pipelines
        interfaces = ir["interfaces"]
        self.publishers = {e["identifier"]: e for e in interfaces["publishers"]}
        self.subscribers = {e["identifier"]: e for e in interfaces["subscribers"]}
        self.services = {e["identifier"]: e for e in interfaces["services"]}
        self.group_of = {d["id"]: d["callback_group"] for d in ir["execution_dags"]}
        self.members: list[str] = []
        self.creates: list[str] = []
        self.methods: list[str] = []
        self.startups = [p.name for p in pipelines if p.trigger["kind"] == "startup"]

    def build(self) -> "InterfacesBuilder":
        """Fill the three lists, in the order the generated code needs them."""
        self._callback_groups()
        self._publishers()
        for pipeline in self.pipelines:
            self._pipeline_trigger(pipeline)
        self._clocks()
        return self

    # ------------------------------------------------------------------ interfaces
    def _callback_groups(self) -> None:
        for group in self.ir["concurrency"]["callback_groups"]:
            self.members.append(f"rclcpp::CallbackGroup::SharedPtr group_{group['name']}_;")
            self.creates.append(
                f"group_{group['name']}_ = node_.create_callback_group("
                f"rclcpp::CallbackGroupType::{group['type']});"
            )

    def _publishers(self) -> None:
        for identifier, publisher in self.publishers.items():
            type_ = message_cpp(publisher["type_symbol"])
            self.members.append(f"rclcpp::Publisher<{type_}>::SharedPtr pub_{identifier}_;")
            qos = qos_expression(publisher["qos"], names.pub_depth(identifier))
            self.creates.append(
                f"pub_{identifier}_ = node_.create_publisher<{type_}>("
                f"{names.pub_topic(identifier)}, {qos});"
            )

    def _pipeline_trigger(self, pipeline: PipelineInfo) -> None:
        """Members, creation and handler declaration for what triggers one pipeline.

        A subscriber that triggers several pipelines gets one subscription per pipeline, because
        the pipelines may sit in different callback groups (SPEC-03 section 4.3)."""
        name, trigger = pipeline.name, pipeline.trigger
        group = self.group_of[name]
        kind = trigger["kind"]
        if kind == "subscriber":
            self._subscription(name, trigger["source_id"], group)
        elif kind == "service":
            self._service(name, trigger["source_id"], group)
        elif kind == "timer":
            self._timer(name, trigger, group)
        else:  # startup
            self.methods.append(f"void run_{name}();")

    def _subscription(self, pipeline: str, source: str, group: str) -> None:
        subscriber = self.subscribers[source]
        type_ = message_cpp(subscriber["type_symbol"])
        member_name = f"sub_{source}_{pipeline}_"
        qos = qos_expression(subscriber["qos"], names.sub_depth(source))
        self.members.append(f"rclcpp::Subscription<{type_}>::SharedPtr {member_name};")
        self.creates += [
            "{",
            "  rclcpp::SubscriptionOptions opts;",
            f"  opts.callback_group = group_{group}_;",
            f"  {member_name} = node_.create_subscription<{type_}>({names.sub_topic(source)}, {qos},",
            f"    [this]({type_}::ConstSharedPtr m) {{ run_{pipeline}(*m); }}, opts);",
            "}",
        ]
        self.methods.append(f"void run_{pipeline}(const {type_} & in);")

    def _service(self, pipeline: str, source: str, group: str) -> None:
        type_ = message_cpp(self.services[source]["type_symbol"])
        self.members.append(f"rclcpp::Service<{type_}>::SharedPtr srv_{source}_;")
        callback = (
            f"[this](const std::shared_ptr<{type_}::Request> req, "
            f"std::shared_ptr<{type_}::Response> res) {{ run_{pipeline}(*req, *res); }}"
        )
        self.creates.append(
            f"srv_{source}_ = node_.create_service<{type_}>({names.service_name(source)}, "
            f"{callback}, rclcpp::ServicesQoS(), group_{group}_);"
        )
        self.methods.append(
            f"void run_{pipeline}(const {type_}::Request & in, {type_}::Response & res);"
        )

    def _timer(self, pipeline: str, trigger: dict, group: str) -> None:
        clock = _CLOCK_MEMBER[trigger["clock"]]
        period = f"rclcpp::Duration(std::chrono::milliseconds({names.period_ms(pipeline)}))"
        self.members += [
            f"rclcpp::TimerBase::SharedPtr timer_{pipeline}_;",
            f"std::atomic<bool> busy_{pipeline}_{{false}};",
        ]
        self.creates.append(
            f"timer_{pipeline}_ = rclcpp::create_timer(&node_, {clock}, {period}, "
            f"[this]() {{ run_{pipeline}(); }}, group_{group}_);"
        )
        self.methods.append(f"void run_{pipeline}();")

    def _clocks(self) -> None:
        timer_clocks = {p.trigger["clock"] for p in self.pipelines if p.trigger["kind"] == "timer"}
        if "system" in timer_clocks:
            self.members.append(
                "rclcpp::Clock::SharedPtr clock_system_{std::make_shared<rclcpp::Clock>(RCL_SYSTEM_TIME)};"
            )
        if "steady" in timer_clocks:
            self.members.append(
                "rclcpp::Clock::SharedPtr clock_steady_{std::make_shared<rclcpp::Clock>(RCL_STEADY_TIME)};"
            )

    # ------------------------------------------------------------------ run methods
    def run_methods(self, class_name: str) -> str:
        """The ``run_<pipeline>`` method definitions, separated by blank lines."""
        return "\n\n".join(self._run_method(class_name, p) for p in self.pipelines)

    def _run_method(self, class_name: str, pipeline: PipelineInfo) -> str:
        name, kind = pipeline.name, pipeline.trigger["kind"]
        response = f"{message_cpp(pipeline.input_type)}::Response" if kind == "service" else None
        reset_response = f" res = {response}();" if response else ""
        call = ["run"]
        if kind in ("subscriber", "service"):
            call.append("in")
        if pipeline.uses_now:
            call.append("now_sec")
        lines = [f"void {class_name}::run_{name}({_run_signature(pipeline)}) {{"]
        if kind == "timer":
            lines += _timer_overrun_guard(name)
        lines += [
            "  const auto run = engine_.enter();",
            f"  if (!run) {{{reset_response} return; }}",
        ]
        if pipeline.uses_now:
            lines.append(f"  const double now_sec = {_clock_now(pipeline)};")
        lines += [
            f"  const auto result = engine_.run_{name}({', '.join(call)});",
            f"  if (!result) {{{reset_response} return; }}  // a numeric fault was reported by the engine (SPEC-02 §7 step 6)",
            "  [[maybe_unused]] const auto & r = *result;",
        ]
        lines += self._publish_lines(pipeline, response)
        lines.append("}")
        return "\n".join(lines)

    def _publish_lines(self, pipeline: PipelineInfo, response: str | None) -> list[str]:
        """Fill the response or build and publish one message per publisher, in ascending id order
        (SPEC-02 section 7, step 5)."""
        by_target: dict[str, list[Assignment]] = {}
        for assignment in pipeline.assignments:
            by_target.setdefault(assignment.target, []).append(assignment)
        lines = []
        for target in sorted(by_target):
            if target == "res":
                lines.append(f"  res = {response}();")
                lines += [
                    f"  {_assign_field(a, 'res.' + a.path, 'r.' + a.member)}"
                    for a in by_target[target]
                ]
            else:
                message = message_cpp(self.publishers[target]["type_symbol"])
                lines += ["  {", f"    {message} out{{}};  // value-initialised"]
                lines += [
                    f"    {_assign_field(a, 'out.' + a.path, 'r.' + a.member)}"
                    for a in by_target[target]
                ]
                lines += [f"    pub_{target}_->publish(out);", "  }"]
        return lines


def _run_signature(pipeline: PipelineInfo) -> str:
    kind = pipeline.trigger["kind"]
    if kind == "subscriber":
        return f"const {message_cpp(pipeline.input_type)} & in"
    if kind == "service":
        service = message_cpp(pipeline.input_type)
        return f"const {service}::Request & in, {service}::Response & res"
    return ""


def _timer_overrun_guard(name: str) -> list[str]:
    """A timer firing while the previous execution still runs is skipped (``ERR_RUN_102``)."""
    return [
        f"  if (busy_{name}_.exchange(true)) {{ engine_.report(r2d::Code::ERR_RUN_102, {names.pipeline(name)}); return; }}",
        f"  struct BusyGuard {{ std::atomic<bool> & b; ~BusyGuard() {{ b.store(false); }} }} busy_guard{{busy_{name}_}};",
    ]


def _clock_now(pipeline: PipelineInfo) -> str:
    trigger = pipeline.trigger
    if trigger["kind"] == "timer" and trigger["clock"] == "system":
        return "system_now()"
    if trigger["kind"] == "timer" and trigger["clock"] == "ros":
        return "node_.get_clock()->now().seconds()"
    return "steady_now()"


def _assign_field(assignment: Assignment, destination: str, source: str) -> str:
    """Statement copying a result value into a message field, converting to the ROS type."""
    ros_type = assignment.ros_type
    if ros_type in ("time", "duration"):
        return f"r2d::set_time({destination}, {source});"
    if ros_type.endswith("[]"):
        return f"r2d::assign_array({destination}, {source});"
    if ros_type in ("string", "bool", "float32", "float64"):
        return f"{destination} = {source};"
    return f"{destination} = static_cast<{ROS_CPP_TYPE[ros_type]}>({source});"
