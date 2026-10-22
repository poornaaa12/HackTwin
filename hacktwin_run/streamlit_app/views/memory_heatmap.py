"""
streamlit_app/views/memory_heatmap.py

4. Memory Heatmaps View
Displays 2D matrix memory write access intensity across data RAM locations.
"""

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


def render_memory_heatmap(st_module, loader):
    """Render Memory Heatmaps section."""
    st_module.header("4. Data Memory Heatmaps")
    st_module.write("Visualizes RAM access spatial and temporal intensity to spot memory write hotspots.")

    runs_df = loader.get_runs_df()
    if runs_df.empty:
        st_module.warning("No runs available.")
        return

    selected_run = st_module.selectbox("Select Simulation Run:", runs_df["run_id"].unique(), key="mem_heat_run")
    matrix_df = loader.get_memory_heatmap_matrix(selected_run)

    if matrix_df.empty:
        st_module.info(f"No memory write operations logged for '{selected_run}'.")
        return

    # Render Heatmap using Seaborn / Matplotlib
    fig, ax = plt.subplots(figsize=(10, 5))
    sns.heatmap(matrix_df, cmap="magma", cbar_kws={'label': 'Write Value'}, ax=ax, linewidths=0.5)
    ax.set_title(f"RAM Data Memory Access Heatmap: {selected_run}")
    ax.set_xlabel("Execution Cycle")
    ax.set_ylabel("RAM Address Location")

    plt.tight_layout()
    st_module.pyplot(fig)
    plt.close(fig)

    st_module.subheader("Memory Write Event Log")
    inst_df, reg_df, mem_df = loader.get_run_trace_df(selected_run)
    if not mem_df.empty:
        st_module.dataframe(mem_df)
