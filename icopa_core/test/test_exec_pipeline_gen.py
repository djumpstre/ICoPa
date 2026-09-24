from __future__ import annotations

import pytest

from icopa_core.task_executor.exec_pipeline_gen import (
    ExecPipelineCompileError,
    compile_experiment_plan,
)


def _scenario_payload() -> dict:
    return {
        "kind": "Scenario",
        "metadata": {"name": "cross-site-zenoh-vm"},
        "spec": {
            "nodes": [
                {"name": "vm_home", "kind": "vm", "labels": {"site": "local"}},
                {"name": "cloud_vm_bw", "kind": "vm", "labels": {"site": "cloud"}},
            ],
            "phaseTemplates": [
                {
                    "name": "preflight",
                    "mode": "sequential",
                    "actions": [
                        {"type": "check_connectivity", "targetRef": "vm:vm_home"},
                    ],
                },
                {
                    "name": "probe",
                    "mode": "sequential",
                    "actions": [
                        {
                            "type": "run_runtime_preset",
                            "targetRef": "vm:vm_home",
                            "preset": "profiling/${variant.probe_preset}",
                            "parameters": {
                                "middleware": "${variant.middleware}",
                                "packet_count": "${variant.packet_count}",
                            },
                        }
                    ],
                },
            ]
        },
    }


def _experiment_payload() -> dict:
    return {
        "kind": "ExperimentPlan",
        "metadata": {
            "name": "exp_plan_two_vm_zenoh_single_run",
            "scenarioRef": "cross-site-zenoh-vm",
        },
        "spec": {
            "execution": {"stopOnFailure": True},
            "selections": {"runtimeEnv": "zenoh-unit8-vm"},
            "phases": [
                {"name": "preflight", "useTemplate": "preflight"},
                {"name": "probe", "useTemplate": "probe"},
            ],
        },
    }


def test_compile_experiment_plan_single_run_success() -> None:
    payload = _experiment_payload()
    payload["spec"]["exploration"] = {
        "axes": {
            "probe_preset": ["latency_sender"],
            "middleware": ["zenoh"],
            "packet_count": [100],
        }
    }
    compiled = compile_experiment_plan(
        generated_name="exp-two-vm-generated",
        experiment_payload=payload,
        scenario_payload=_scenario_payload(),
        max_total_runs=2,
    )

    assert compiled["kind"] == "GeneratedExperimentPlan"
    assert compiled["metadata"]["name"] == "exp-two-vm-generated"
    assert compiled["total_generated_runs"] == 1
    assert len(compiled["runs"]) == 1
    assert compiled["runs"][0]["run_id"] == "run-001"
    assert compiled["execution_policy"]["stop_on_failure"] is True
    assert compiled["execution_policy"]["connectivity_precheck"] is True


def test_compile_experiment_plan_two_runs_success() -> None:
    payload = _experiment_payload()
    payload["spec"]["exploration"] = {
        "axes": {
            "probe_preset": ["latency_sender", "latency_responder"],
            "middleware": ["zenoh"],
            "packet_count": [100],
        }
    }
    compiled = compile_experiment_plan(
        generated_name="exp-two-runs",
        experiment_payload=payload,
        scenario_payload=_scenario_payload(),
        max_total_runs=2,
    )

    assert compiled["total_generated_runs"] == 2
    assert [item["run_id"] for item in compiled["runs"]] == ["run-001", "run-002"]


def test_compile_experiment_plan_rejects_execution_runs_field() -> None:
    payload = _experiment_payload()
    payload["spec"]["execution"]["runs"] = 3
    payload["spec"]["exploration"] = {
        "axes": {
            "probe_preset": ["latency_sender"],
            "middleware": ["zenoh"],
            "packet_count": [100],
        }
    }

    with pytest.raises(ExecPipelineCompileError, match="execution.runs is not supported"):
        compile_experiment_plan(
            generated_name="exp-runs-field",
            experiment_payload=payload,
            scenario_payload=_scenario_payload(),
            max_total_runs=2,
        )


def test_compile_experiment_plan_materializes_concrete_actions() -> None:
    payload = _experiment_payload()
    payload["spec"]["exploration"] = {
        "axes": {
            "probe_preset": ["latency_sender"],
            "middleware": ["zenoh"],
            "packet_count": [100],
        }
    }
    compiled = compile_experiment_plan(
        generated_name="exp-concrete-actions",
        experiment_payload=payload,
        scenario_payload=_scenario_payload(),
        max_total_runs=2,
    )

    probe_phase = compiled["runs"][0]["phases"][1]
    action = probe_phase["actions"][0]
    assert probe_phase["name"] == "probe"
    assert action["preset"] == "profiling/latency_sender"
    assert action["parameters"]["middleware"] == "zenoh"
    assert action["parameters"]["packet_count"] == 100


def test_compile_experiment_plan_collect_metrics_template_success() -> None:
    payload = _experiment_payload()
    payload["spec"]["phases"].append({"name": "collect_metrics", "useTemplate": "collect_metrics"})
    payload["spec"]["exploration"] = {
        "axes": {
            "probe_preset": ["latency_sender"],
            "middleware": ["zenoh"],
            "packet_count": [100],
        }
    }

    scenario = _scenario_payload()
    scenario["spec"]["phaseTemplates"].append(
        {
            "name": "collect_metrics",
            "mode": "sequential",
            "actions": [{"type": "collect_metrics", "targetRef": "vm:vm_home"}],
        }
    )

    compiled = compile_experiment_plan(
        generated_name="exp-collect-metrics",
        experiment_payload=payload,
        scenario_payload=scenario,
        max_total_runs=2,
    )

    collect_phase = compiled["runs"][0]["phases"][-1]
    assert collect_phase["source_template"] == "collect_metrics"
    assert collect_phase["actions"][0]["type"] == "collect_metrics"


def test_compile_experiment_plan_rejects_collect_template_name() -> None:
    payload = _experiment_payload()
    payload["spec"]["phases"].append({"name": "collect", "useTemplate": "collect"})
    payload["spec"]["exploration"] = {
        "axes": {
            "probe_preset": ["latency_sender"],
            "middleware": ["zenoh"],
            "packet_count": [100],
        }
    }

    scenario = _scenario_payload()
    scenario["spec"]["phaseTemplates"].append(
        {
            "name": "collect_metrics",
            "mode": "sequential",
            "actions": [{"type": "collect_metrics", "targetRef": "vm:vm_home"}],
        }
    )

    with pytest.raises(ExecPipelineCompileError, match="Phase template 'collect' was not found in scenario"):
        compile_experiment_plan(
            generated_name="exp-collect-strict",
            experiment_payload=payload,
            scenario_payload=scenario,
            max_total_runs=2,
        )


def test_compile_experiment_plan_rejects_steps_in_scenario_phase_templates() -> None:
    payload = _experiment_payload()
    payload["spec"]["exploration"] = {
        "axes": {
            "probe_preset": ["latency_sender"],
            "middleware": ["zenoh"],
            "packet_count": [100],
        }
    }
    scenario = _scenario_payload()
    scenario["spec"]["phaseTemplates"] = [
        {
            "name": "probe",
            "mode": "sequential",
            "steps": [
                {
                    "action": "run_runtime_preset",
                    "target": "vm_home",
                    "group": "profiling",
                    "preset": "${variant.probe_preset}",
                    "parameters": {"middleware": "${variant.middleware}"},
                }
            ],
        }
    ]
    payload["spec"]["phases"] = [{"name": "probe", "useTemplate": "probe"}]

    with pytest.raises(ExecPipelineCompileError, match="\\.steps is not supported"):
        compile_experiment_plan(
            generated_name="exp-probe-steps-template",
            experiment_payload=payload,
            scenario_payload=scenario,
            max_total_runs=2,
        )


def test_compile_experiment_plan_rejects_steps_in_experiment_phase_override() -> None:
    payload = _experiment_payload()
    payload["spec"]["exploration"] = {
        "axes": {
            "probe_preset": ["latency_sender"],
            "middleware": ["zenoh"],
            "packet_count": [100],
        }
    }
    payload["spec"]["phases"] = [
        {
            "name": "probe",
            "steps": [
                {
                    "action": "run_runtime_preset",
                    "target": "vm_home",
                    "group": "profiling",
                    "preset": "${variant.probe_preset}",
                    "parameters": {"middleware": "${variant.middleware}"},
                }
            ],
        }
    ]

    with pytest.raises(ExecPipelineCompileError, match="\\.steps is not supported"):
        compile_experiment_plan(
            generated_name="exp-probe-steps-override",
            experiment_payload=payload,
            scenario_payload=_scenario_payload(),
            max_total_runs=2,
        )


def test_compile_experiment_plan_supports_target_ref_site_fan_out() -> None:
    payload = _experiment_payload()
    payload["spec"]["exploration"] = {
        "axes": {
            "probe_preset": ["latency_sender"],
            "middleware": ["zenoh"],
            "packet_count": [100],
        }
    }
    payload["spec"]["phases"] = [{"name": "probe", "useTemplate": "probe"}]

    scenario = _scenario_payload()
    scenario["spec"]["phaseTemplates"] = [
        {
            "name": "probe",
            "mode": "sequential",
            "actions": [
                {
                    "type": "run_runtime_preset",
                    "preset": "profiling/${variant.probe_preset}",
                    "targetRef": {"kind": "site", "name": "local"},
                }
            ],
        }
    ]

    compiled = compile_experiment_plan(
        generated_name="exp-site-target-fan-out",
        experiment_payload=payload,
        scenario_payload=scenario,
        max_total_runs=2,
    )

    actions = compiled["runs"][0]["phases"][0]["actions"]
    assert len(actions) == 1
    assert actions[0]["target"] == "vm_home"
    assert actions[0]["targetRef"] == {"kind": "vm", "name": "vm_home"}


def test_compile_experiment_plan_connectivity_precheck_override() -> None:
    payload = _experiment_payload()
    payload["spec"]["execution"]["connectivityPrecheck"] = False
    payload["spec"]["exploration"] = {
        "axes": {
            "probe_preset": ["latency_sender"],
            "middleware": ["zenoh"],
            "packet_count": [100],
        }
    }
    compiled = compile_experiment_plan(
        generated_name="exp-connectivity-precheck-off",
        experiment_payload=payload,
        scenario_payload=_scenario_payload(),
        max_total_runs=2,
    )
    assert compiled["execution_policy"]["connectivity_precheck"] is False


def test_compile_experiment_plan_rejects_phase_alias_lists() -> None:
    payload = _experiment_payload()
    payload["spec"].pop("phases", None)
    payload["spec"]["phasesPrerun"] = [{"name": "preflight", "useTemplate": "preflight"}]
    payload["spec"]["phasesInEachRun"] = [{"name": "probe", "useTemplate": "probe"}]
    payload["spec"]["exploration"] = {
        "axes": {
            "probe_preset": ["latency_sender"],
            "middleware": ["zenoh"],
            "packet_count": [100],
        }
    }
    with pytest.raises(ExecPipelineCompileError, match="phasesPrerun/phasesInEachRun"):
        compile_experiment_plan(
            generated_name="exp-phase-aliases",
            experiment_payload=payload,
            scenario_payload=_scenario_payload(),
            max_total_runs=2,
        )


def test_compile_experiment_plan_rejects_exec_action_aliases() -> None:
    payload = _experiment_payload()
    payload["spec"]["phases"] = [{"name": "setup_routing_to_cloud", "useTemplate": "setup_routing_to_cloud"}]
    payload["spec"]["exploration"] = {"axes": {"probe_preset": ["latency_sender"]}}

    scenario = _scenario_payload()
    scenario["spec"]["phaseTemplates"] = [
        {
            "name": "setup_routing_to_cloud",
            "mode": "sequential",
            "actions": [
                {
                    "execAction": "vm_runtime:setup_zenoh_routing/jazzy_v1",
                    "targetRef": "vm:cloud_vm_bw",
                    "parameters": {
                        "preset_container_id": "zenoh_router:jazzy_v1",
                        "exec_cmd_type": "vm_ce_router_exec_cmd",
                    },
                },
                {
                    "execAction": "wait",
                    "parameters": {"seconds": 3},
                },
                {
                    "execAction": "vm_general:run_stress_cpu_ram",
                    "targetRef": "vm:vm_home",
                    "parameters": {"cpu_cores": 2, "mem_gb": 1},
                },
            ],
        }
    ]

    with pytest.raises(ExecPipelineCompileError, match="execAction is not supported"):
        compile_experiment_plan(
            generated_name="exp-exec-action-normalization",
            experiment_payload=payload,
            scenario_payload=scenario,
            max_total_runs=2,
        )


def test_compile_experiment_plan_supports_string_site_target_ref() -> None:
    payload = _experiment_payload()
    payload["spec"]["phases"] = [{"name": "probe", "useTemplate": "probe"}]
    payload["spec"]["exploration"] = {"axes": {"probe_preset": ["latency_sender"]}}

    scenario = _scenario_payload()
    scenario["spec"]["phaseTemplates"] = [
        {
            "name": "probe",
            "mode": "sequential",
            "actions": [
                {
                    "type": "run_runtime_preset",
                    "preset": "profiling/${variant.probe_preset}",
                    "targetRef": "site:local",
                }
            ],
        }
    ]

    compiled = compile_experiment_plan(
        generated_name="exp-string-site-target-ref",
        experiment_payload=payload,
        scenario_payload=scenario,
        max_total_runs=2,
    )

    actions = compiled["runs"][0]["phases"][0]["actions"]
    assert len(actions) == 1
    assert actions[0]["target"] == "vm_home"
    assert actions[0]["targetRef"] == {"kind": "vm", "name": "vm_home"}


def _scenario_payload_with_named_edges() -> dict:
    return {
        "kind": "Scenario",
        "metadata": {"name": "multi-edge-zenoh-vm"},
        "spec": {
            "nodes": [
                {"name": "vm_home", "kind": "vm", "labels": {"site": "local"}},
                {"name": "cloud_vm_bw", "kind": "vm", "labels": {"site": "cloud"}},
                {"name": "vm_lab_edge", "kind": "vm", "labels": {"site": "edge"}},
            ],
            "graph": {
                "edges": [
                    {
                        "name": "robot_to_cloud",
                        "from": "vm_home",
                        "to": "cloud_vm_bw",
                        "type": "robot2cloud",
                        "link": ["wan", "wifi"],
                    },
                    {
                        "name": "robot_to_edge",
                        "from": "vm_home",
                        "to": "vm_lab_edge",
                        "type": "robot2edge",
                        "link": ["wifi"],
                    },
                ]
            },
            "phaseTemplates": [
                {
                    "name": "preflight",
                    "mode": "sequential",
                    "actions": [
                        {
                            "type": "check_connectivity",
                            "targetRef": "vm:vm_home",
                        }
                    ],
                },
                {
                    "name": "probe",
                    "mode": "sequential",
                    "actions": [
                        {
                            "type": "run_runtime_preset",
                            "preset": "profiling/latency_responder",
                            "targetRef": "vm:${edge.to}",
                        },
                        {
                            "type": "run_runtime_preset",
                            "preset": "profiling/latency_sender",
                            "targetRef": "vm:${edge.from}",
                            "parameters": {
                                "edge_name": "${edge.name}",
                                "edge_type": "${edge.type}",
                            },
                        },
                    ],
                },
                {
                    "name": "collect_metrics",
                    "mode": "sequential",
                    "actions": [
                        {
                            "type": "collect_metrics",
                            "targetRef": "vm:${edge.from}",
                        }
                    ],
                },
            ],
        },
    }


def _experiment_payload_with_edge_refs() -> dict:
    return {
        "kind": "ExperimentPlan",
        "metadata": {
            "name": "exp-edge-based",
            "scenarioRef": "multi-edge-zenoh-vm",
        },
        "spec": {
            "execution": {"stopOnFailure": True},
            "exploration": {"axes": {"payload_size": ["16KB", "32KB"]}},
            "selections": {"runtimeEnv": "zenoh-unit8-vm"},
            "phases": [
                {
                    "name": "probe",
                    "useTemplate": "probe",
                    "edgeRefs": ["robot_to_cloud", "robot_to_edge"],
                },
                {
                    "name": "collect_metrics",
                    "useTemplate": "collect_metrics",
                    "edgeRefs": ["robot_to_cloud", "robot_to_edge"],
                },
            ],
        },
    }


def test_compile_experiment_plan_expands_runs_by_variant_and_edge() -> None:
    compiled = compile_experiment_plan(
        generated_name="exp-edge-generated",
        experiment_payload=_experiment_payload_with_edge_refs(),
        scenario_payload=_scenario_payload_with_named_edges(),
        max_total_runs=10,
    )
    assert compiled["total_generated_runs"] == 4
    assert [run["run_id"] for run in compiled["runs"]] == ["run-001", "run-002", "run-003", "run-004"]
    assert {run["edge"]["name"] for run in compiled["runs"]} == {"robot_to_cloud", "robot_to_edge"}


def test_compile_experiment_plan_renders_edge_placeholders_into_actions() -> None:
    compiled = compile_experiment_plan(
        generated_name="exp-edge-placeholders",
        experiment_payload=_experiment_payload_with_edge_refs(),
        scenario_payload=_scenario_payload_with_named_edges(),
        max_total_runs=10,
    )

    first_run = compiled["runs"][0]
    probe_actions = first_run["phases"][0]["actions"]
    assert first_run["edge"]["name"] in {"robot_to_cloud", "robot_to_edge"}
    assert probe_actions[0]["target"] == first_run["edge"]["to"]
    assert probe_actions[1]["target"] == first_run["edge"]["from"]
    assert probe_actions[1]["parameters"]["edge_name"] == first_run["edge"]["name"]
    assert probe_actions[1]["parameters"]["edge_type"] == first_run["edge"]["type"]


def test_compile_experiment_plan_rejects_unknown_phase_edge_refs() -> None:
    payload = _experiment_payload_with_edge_refs()
    payload["spec"]["phases"][0]["edgeRefs"] = ["edge_not_found"]

    with pytest.raises(ExecPipelineCompileError, match="edgeRefs contains unknown edge names"):
        compile_experiment_plan(
            generated_name="exp-edge-unknown",
            experiment_payload=payload,
            scenario_payload=_scenario_payload_with_named_edges(),
            max_total_runs=10,
        )


def test_compile_experiment_plan_requires_edge_refs_for_edge_placeholders() -> None:
    payload = _experiment_payload_with_edge_refs()
    payload["spec"]["phases"][0].pop("edgeRefs")

    with pytest.raises(ExecPipelineCompileError, match="requires experiment.spec.phases\\[0\\]\\.edgeRefs"):
        compile_experiment_plan(
            generated_name="exp-edge-missing-selector",
            experiment_payload=payload,
            scenario_payload=_scenario_payload_with_named_edges(),
            max_total_runs=10,
        )


def test_compile_experiment_plan_supports_exec_edge_blocks() -> None:
    payload = {
        "kind": "ExperimentPlan",
        "metadata": {"name": "exp-exec-edge", "scenarioRef": "multi-edge-zenoh-vm"},
        "spec": {
            "execution": {"stopOnFailure": True},
            "exploration": {"axes": {"payload_size": ["16KB", "32KB"]}},
            "selections": {"runtimeEnv": "zenoh-unit8-vm"},
            "phases": [
                {"name": "global-preflight", "useTemplate": "preflight"},
            ],
            "execEdge": [
                {
                    "edgeRefs": ["robot_to_cloud"],
                    "phases": [{"name": "probe-cloud", "useTemplate": "probe"}],
                },
                {
                    "edgeRefs": ["robot_to_edge"],
                    "phases": [{"name": "probe-edge", "useTemplate": "probe"}],
                },
            ],
        },
    }
    compiled = compile_experiment_plan(
        generated_name="exp-exec-edge-generated",
        experiment_payload=payload,
        scenario_payload=_scenario_payload_with_named_edges(),
        max_total_runs=10,
    )

    assert compiled["total_generated_runs"] == 4
    for run in compiled["runs"]:
        phases = run["phases"]
        assert len(phases) == 2
        assert phases[0]["name"] == "global-preflight"
        if run["edge"]["name"] == "robot_to_cloud":
            assert phases[1]["name"] == "probe-cloud"
        if run["edge"]["name"] == "robot_to_edge":
            assert phases[1]["name"] == "probe-edge"


def test_compile_experiment_plan_rejects_exec_edge_block_without_edge_refs() -> None:
    payload = _experiment_payload_with_edge_refs()
    payload["spec"].pop("phases")
    payload["spec"]["execEdge"] = [
        {
            "phases": [{"name": "probe", "useTemplate": "probe"}],
        }
    ]
    with pytest.raises(ExecPipelineCompileError, match="experiment.spec.execEdge\\[0\\]\\.edgeRefs is required"):
        compile_experiment_plan(
            generated_name="exp-exec-edge-missing-edge-refs",
            experiment_payload=payload,
            scenario_payload=_scenario_payload_with_named_edges(),
            max_total_runs=10,
        )


def test_compile_experiment_plan_supports_exec_edge_scoped_exploration_axes() -> None:
    payload = {
        "kind": "ExperimentPlan",
        "metadata": {"name": "exp-exec-edge-scoped-axes", "scenarioRef": "multi-edge-zenoh-vm"},
        "spec": {
            "execution": {"stopOnFailure": True},
            "selections": {"runtimeEnv": "zenoh-unit8-vm"},
            "execEdge": [
                {
                    "edgeRefs": ["robot_to_cloud"],
                    "exploration": {
                        "icopa_sender:payload_size": ["16KB", "32KB"],
                        "icopa_sender:frequency_hz": [30],
                    },
                    "phases": [{"name": "probe-cloud", "useTemplate": "probe"}],
                },
                {
                    "edgeRefs": ["robot_to_edge"],
                    "exploration": {
                        "icopa_sender:payload_size": ["8KB"],
                        "icopa_sender:frequency_hz": [60, 120],
                    },
                    "phases": [{"name": "probe-edge", "useTemplate": "probe"}],
                },
            ],
        },
    }
    compiled = compile_experiment_plan(
        generated_name="exp-exec-edge-scoped-axes-generated",
        experiment_payload=payload,
        scenario_payload=_scenario_payload_with_named_edges(),
        max_total_runs=10,
    )

    assert compiled["total_generated_runs"] == 4
    cloud_variants = [
        run["variant_values"]
        for run in compiled["runs"]
        if run.get("edge", {}).get("name") == "robot_to_cloud"
    ]
    edge_variants = [
        run["variant_values"]
        for run in compiled["runs"]
        if run.get("edge", {}).get("name") == "robot_to_edge"
    ]
    assert len(cloud_variants) == 2
    assert len(edge_variants) == 2
    assert {item["icopa_sender:payload_size"] for item in cloud_variants} == {"16KB", "32KB"}
    assert {item["icopa_sender:frequency_hz"] for item in cloud_variants} == {30}
    assert {item["icopa_sender:payload_size"] for item in edge_variants} == {"8KB"}
    assert {item["icopa_sender:frequency_hz"] for item in edge_variants} == {60, 120}
