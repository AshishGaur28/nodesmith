"""The ROS node: its members, the code that creates them, and the run method of each pipeline.

``NodeBuilder`` collects three lists that the templates (``node.hpp.j2``, ``node.cpp.j2``)
paste in: ``members`` (fields of the class), ``creates`` (statements of ``create_interfaces``)
and ``methods`` (declarations of the ``run_<pipeline>`` methods). ``run_method`` writes the
body of one of those methods: it enters the run gate, evaluates the pipeline, then commits
state and publishes (SPEC-02 section 7, steps 5 and 6; SPEC-12 sections 3 to 6).
"""

from .dag import Assignment, PipelineInfo
from .literals import cpp_string
from .naming import ROS_CPP_TYPE, member, message_cpp

_CLOCK_MEMBER = {"steady": "clock_steady_", "system": "clock_system_", "ros": "get_clock()"}


def qos_expression(qos: dict) -> str:
    """The ``rclcpp::QoS`` built for an endpoint's QoS settings."""
    if qos["history"] == "keep_all":
        text = "rclcpp::QoS(rclcpp::KeepAll())"
    else:
        text = f"rclcpp::QoS({qos['depth']})"
    text += ".reliable()" if qos["reliability"] == "reliable" else ".best_effort()"
    text += (
        ".transient_local()" if qos["durability"] == "transient_local" else ".durability_volatile()"
    )
    return text


class NodeBuilder:
    """Collects the members, creation statements and method declarations of the node class."""

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

    def build(self) -> "NodeBuilder":
        """Fill the three lists, in the order the generated code needs them."""
        self._callback_groups()
        self._publishers()
        for pipeline in self.pipelines:
            self._pipeline_trigger(pipeline)
        self._clocks()
        self._diagnostics()
        return self

    # ------------------------------------------------------------------ interfaces
    def _callback_groups(self) -> None:
        for group in self.ir["concurrency"]["callback_groups"]:
            self.members.append(f"rclcpp::CallbackGroup::SharedPtr group_{group['name']}_;")
            self.creates.append(
                f"group_{group['name']}_ = create_callback_group(rclcpp::CallbackGroupType::{group['type']});"
            )
        self.members.append("rclcpp::CallbackGroup::SharedPtr group_diagnostics_;")
        self.creates.append(
            "group_diagnostics_ = create_callback_group(rclcpp::CallbackGroupType::MutuallyExclusive);"
        )

    def _publishers(self) -> None:
        for identifier, publisher in self.publishers.items():
            type_ = message_cpp(publisher["type_symbol"])
            self.members.append(f"rclcpp::Publisher<{type_}>::SharedPtr pub_{identifier}_;")
            topic, qos = cpp_string(publisher["topic"]), qos_expression(publisher["qos"])
            self.creates.append(f"pub_{identifier}_ = create_publisher<{type_}>({topic}, {qos});")

    def _pipeline_trigger(self, pipeline: PipelineInfo) -> None:
        """Members, creation and method declaration for what triggers one pipeline.

        A subscriber that triggers several pipelines gets one subscription per pipeline, because
        the pipelines may sit in different callback groups (SPEC-03 section 4.3)."""
        name, trigger = pipeline.name, pipeline.trigger
        group = self.group_of[name]
        if pipeline.uses_dt:
            self.members += [f"double last_{name}_{{0.0}};", f"bool have_last_{name}_{{false}};"]
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
        topic, qos = cpp_string(subscriber["topic"]), qos_expression(subscriber["qos"])
        self.members.append(f"rclcpp::Subscription<{type_}>::SharedPtr {member_name};")
        self.creates += [
            "{",
            "  rclcpp::SubscriptionOptions opts;",
            f"  opts.callback_group = group_{group}_;",
            f"  {member_name} = create_subscription<{type_}>({topic}, {qos},",
            f"    [this]({type_}::ConstSharedPtr m) {{ run_{pipeline}(*m); }}, opts);",
            "}",
        ]
        self.methods.append(f"void run_{pipeline}(const {type_} & in);")

    def _service(self, pipeline: str, source: str, group: str) -> None:
        service = self.services[source]
        type_ = message_cpp(service["type_symbol"])
        self.members.append(f"rclcpp::Service<{type_}>::SharedPtr srv_{source}_;")
        callback = (
            f"[this](const std::shared_ptr<{type_}::Request> req, "
            f"std::shared_ptr<{type_}::Response> res) {{ run_{pipeline}(*req, *res); }}"
        )
        self.creates.append(
            f"srv_{source}_ = create_service<{type_}>({cpp_string(service['service_name'])}, "
            f"{callback}, rclcpp::ServicesQoS(), group_{group}_);"
        )
        self.methods.append(
            f"void run_{pipeline}(const {type_}::Request & in, {type_}::Response & res);"
        )

    def _timer(self, pipeline: str, trigger: dict, group: str) -> None:
        clock = _CLOCK_MEMBER[trigger["clock"]]
        period = f"rclcpp::Duration(std::chrono::milliseconds({trigger['period_ms']}))"
        self.members += [
            f"rclcpp::TimerBase::SharedPtr timer_{pipeline}_;",
            f"std::atomic<bool> busy_{pipeline}_{{false}};",
        ]
        self.creates.append(
            f"timer_{pipeline}_ = rclcpp::create_timer(this, {clock}, {period}, "
            f"[this]() {{ run_{pipeline}(); }}, group_{group}_);"
        )
        self.methods.append(f"void run_{pipeline}();")

    # ------------------------------------------------------------------ clocks and diagnostics
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

    def _diagnostics(self) -> None:
        """The ``/diagnostics`` publisher and the timer that drains the diagnostics ring (SPEC-12 section 7)."""
        self.members += [
            "rclcpp::Publisher<diagnostic_msgs::msg::DiagnosticArray>::SharedPtr diag_pub_;",
            "rclcpp::TimerBase::SharedPtr diag_timer_;",
            "rclcpp::Clock::SharedPtr clock_diag_{std::make_shared<rclcpp::Clock>(RCL_STEADY_TIME)};",
        ]
        self.creates += [
            'diag_pub_ = create_publisher<diagnostic_msgs::msg::DiagnosticArray>("/diagnostics", rclcpp::QoS(10));',
            "diag_timer_ = rclcpp::create_timer(this, clock_diag_, "
            "rclcpp::Duration(std::chrono::seconds(1)), [this]() { drain_diagnostics(); }, "
            "group_diagnostics_);",
        ]

    # ------------------------------------------------------------------ run methods
    def run_methods(self, class_name: str, result_types: dict[str, str]) -> str:
        """The ``run_<pipeline>`` method definitions, separated by blank lines."""
        return "\n\n".join(
            self._run_method(class_name, p, result_types[p.name]) for p in self.pipelines
        )

    def _run_method(self, class_name: str, pipeline: PipelineInfo, result_type: str) -> str:
        name, kind = pipeline.name, pipeline.trigger["kind"]
        response = f"{message_cpp(pipeline.input_type)}::Response" if kind == "service" else None
        lines = [f"void {class_name}::run_{name}({_run_signature(pipeline)}) {{"]
        if kind == "timer":
            lines += _timer_overrun_guard(name)
        reset_response = f" res = {response}();" if response else ""
        lines += [
            "  r2d::RunGate::Scope run(gate_);",
            f"  if (!run) {{{reset_response} return; }}",
            "  const auto params = params_.acquire();  // one snapshot for the whole execution (SPEC-12 §5)",
        ]
        lines += _sample_clock_lines(pipeline)
        lines += _evaluate_lines(pipeline, result_type, response)
        lines += [
            f"  state_.{member(state)} = r.{result_member};  // step 5: commit"
            for state, _, result_member in pipeline.state_members
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
        f"  if (busy_{name}_.exchange(true)) {{ diag_.report(r2d::Code::ERR_RUN_102, {cpp_string(name)}); return; }}",
        f"  struct BusyGuard {{ std::atomic<bool> & b; ~BusyGuard() {{ b.store(false); }} }} busy_guard{{busy_{name}_}};",
    ]


def _clock_now(pipeline: PipelineInfo) -> str:
    trigger = pipeline.trigger
    if trigger["kind"] == "timer" and trigger["clock"] == "system":
        return "system_now()"
    if trigger["kind"] == "timer" and trigger["clock"] == "ros":
        return "get_clock()->now().seconds()"
    return "steady_now()"


def _sample_clock_lines(pipeline: PipelineInfo) -> list[str]:
    """Sample the clock once per execution, and the time since the previous one if needed."""
    lines = []
    if pipeline.uses_now:
        lines.append(f"  const double now_sec = {_clock_now(pipeline)};")
    if pipeline.uses_dt:
        name = pipeline.name
        lines += [
            f"  const double dt_sec = have_last_{name}_ ? now_sec - last_{name}_ : 0.0;",
            f"  have_last_{name}_ = true;",
            f"  last_{name}_ = now_sec;",
        ]
    return lines


def _evaluate_lines(pipeline: PipelineInfo, result_type: str, response: str | None) -> list[str]:
    """Call the pure evaluation function; on a numeric fault report it and stop (step 6)."""
    name = pipeline.name
    has_input = pipeline.trigger["kind"] in ("subscriber", "service")
    arguments = ("in, " if has_input else "") + "*params, state_, "
    arguments += "now_sec, " if pipeline.uses_now else "0.0, "
    arguments += "dt_sec, " if pipeline.uses_dt else "0.0, "
    lines = [
        f"  {result_type} r;",
        f"  if (!eval_{name}({arguments}r)) {{",
        f"    diag_.report(r2d::Code::ERR_RUN_101, {cpp_string(name)});  // preallocated ring: no allocation, no logging here",
    ]
    if response:
        lines.append(
            f"    res = {response}();  // a fault returns the default response (SPEC-02 §7 step 6)"
        )
    lines += ["    return;", "  }"]
    return lines


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
