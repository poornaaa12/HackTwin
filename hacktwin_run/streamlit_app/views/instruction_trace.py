"""
streamlit_app/views/instruction_trace.py

2. Instruction Execution Timeline View
Plots PC step timeline, instruction frequency spectrum, and trace step table.
"""

import matplotlib.pyplot as plt
import pandas as pd


def render_instruction_trace(st_module, loader):
    """Render the Instruction Execution Timeline section."""
    st_module.header("2. Instruction Execution Timeline")
    st_module.write("Step-by-step Program Counter (PC) trajectory and instruction density across execution cycles.")

    runs_df = loader.get_runs_df()
    if runs_df.empty:
        st_module.warning("No runs available.")
        return

    selected_run = st_module.selectbox("Select Simulation Run for Trace Analysis:", runs_df["run_id"].unique(), key="inst_trace_run")

    inst_df, reg_df, mem_df = loader.get_run_trace_df(selected_run)
    if inst_df.empty:
        st_module.info(f"No instruction trace available for '{selected_run}'.")
        return

    # Plot PC vs Cycle Step
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)

    ax1.plot(inst_df["cycle"], inst_df["pc"], color="#00F2FE", marker="o", markersize=3, linewidth=1.5)
    ax1.set_title(f"Program Counter (PC) Execution Trajectory: {selected_run}")
    ax1.set_ylabel("PC Address")
    ax1.grid(True, linestyle="--", alpha=0.5)

    # PC Frequency Distribution
    pc_counts = inst_df["pc"].value_counts().sort_index()
    ax2.bar(pc_counts.index, pc_counts.values, color="#4FACFE", alpha=0.8)
    ax2.set_title("PC Instruction Frequency Distribution")
    ax2.set_xlabel("PC Address")
    ax2.set_ylabel("Execution Count")
    ax2.grid(True, linestyle="--", alpha=0.5)

    plt.tight_layout()
    st_module.pyplot(fig)
    plt.close(fig)

    # Step Data Table
    st_module.subheader("Trace Step Stream")
    st_module.dataframe(inst_df.head(100))
