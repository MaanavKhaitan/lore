"""Run tau3-bench with the lore-guarded airline domain.

Registers ``airline_lore`` (guarded env + stock airline tasks) and then
hands over to the stock tau2 CLI, so every tau2 flag works unchanged:

  uv run --with-editable .context/tau2-bench python -m benchmarks.tau3_airline.harness.run_lore run \\
      --domain airline_lore --agent-llm claude-sonnet-5 \\
      --user-llm claude-sonnet-5 --task-ids 1 26 43 --num-trials 1 \\
      --save-to probe_arm_b

Baseline (Arm A) runs use ``--domain airline`` with the same CLI.
"""

from . import lore_env


def main():
    lore_env.register()
    from tau2.cli import main as tau2_main
    tau2_main()


if __name__ == "__main__":
    main()
