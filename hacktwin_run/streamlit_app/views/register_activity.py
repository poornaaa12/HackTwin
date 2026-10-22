"""
streamlit_app/views/register_activity.py

3. Register Activity Graphs View
Plots A-register and D-register updates, state changes, and churn rate over execution cycles.
"""

import matplotlib.pyplot as plt
import pandas as pd


def render_register_activity(st_module, loader):
    """Render Register Activity Graphs section."""
    st_module.header("3. Register Activity Graphs")
    st_module.write("Monitors A-Register (addressing) and D-Register (ALU accumulation) activity patterns.")

    runs_df = loader.get_runs_df()
    if runs_df.empty:
        st_module.warning("No simulation runs available.")
        return

    selected_run = st_module.selectbox("Select Simulation Run:", runs_df["run_id"].unique(), key="reg_act_run")
    inst_df, reg_df, mem_df = loader.get_run_trace_df(selected_run)

    if reg_df.empty or "A" not in reg_df.columns or "D" not in reg_df.columns:
        st_module.info(f"No register activity snapshots logged for '{selected_run}'.")
        return

    # Plot A and D register values over cycles
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)

    ax1.step(reg_df["cycle"], reg_df["A"], color="#10B981", where="post", label="A Register")
    ax1.set_title(f"A-Register Address Trajectory: {selected_run}")
    ax1.set_ylabel("A Register Value")
    ax1.grid(True, linestyle="--", alpha=0.5)
    ax1.legend()

    ax2.step(reg_df["cycle"], reg_df["D"], color="#F59E0B", where="post", label="D Register")
    ax2.set_title("D-Register ALU Data Accumulation")
    ax2.set_xlabel("Execution Cycle")
    ax2.set_ylabel("D Register Value")
    ax2.grid(True, linestyle="--", alpha=0.5)
    ax2.legend()

    plt.tight_layout()
    st_module.pyplot(fig)
    plt.close(fig)

    # Churn Metrics Breakdown
    a_changes = (reg_df["A"].diff() != 0).sum()
    d_changes = (reg_df["D"].diff() != 0).sum()
    total_cycles = len(reg_df)

    col1, col2 = st_module.columns(2)
    with col1:
        st_module.metric("A-Register Modifications", f"{a_changes} updates", f"{round((a_changes/max(total_cycles,1))*100, 1)}% churn")
    with col2:
        st_module.metric("D-Register Modifications", f"{d_changes} updates", f"{round((d_changes/max(total_cycles,1))*100, 1)}% churn")
