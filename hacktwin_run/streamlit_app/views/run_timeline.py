"""
streamlit_app/views/run_timeline.py

1. Run Timeline Visualization View
Displays simulation run timeline, cycle duration bars, and elapsed time metrics.
"""

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


def render_run_timeline(st_module, loader):
    """Render the Run Timeline visualization section."""
    st_module.header("1. Run Timeline Visualization")
    st_module.write("Timeline of executed HDL simulation runs, cycle durations, and execution overheads.")

    df = loader.get_runs_df()
    if df.empty:
        st_module.warning("No simulation runs found in TwinStore.")
        return

    # Filter controls
    col1, col2 = st_module.columns(2)
    with col1:
        selected_design = st_module.selectbox("Filter Design ID:", ["All"] + sorted(list(df["design_id"].unique())))
    with col2:
        selected_bench = st_module.selectbox("Filter Benchmark:", ["All"] + sorted(list(df["benchmark_id"].unique())))

    filtered_df = df.copy()
    if selected_design != "All":
        filtered_df = filtered_df[filtered_df["design_id"] == selected_design]
    if selected_bench != "All":
        filtered_df = filtered_df[filtered_df["benchmark_id"] == selected_bench]

    st_module.subheader("Simulation Run Execution Timeline")

    # Plot Gantt-style timeline chart
    fig, ax = plt.subplots(figsize=(10, 4))
    sns.barplot(data=filtered_df, x="cycle_count", y="run_id", hue="design_id", palette="viridis", ax=ax)
    ax.set_title("Cycle Count Duration per Run")
    ax.set_xlabel("Execution Cycles")
    ax.set_ylabel("Run Identifier")
    plt.tight_layout()

    st_module.pyplot(fig)
    plt.close(fig)

    # Detailed Table
    st_module.subheader("Run Telemetry Detail Table")
    st_module.dataframe(filtered_df[["run_id", "design_id", "benchmark_id", "repeat_index", "cycle_count", "elapsed_s"]])
