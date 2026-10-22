"""
streamlit_app/views/design_comparison.py

6. Design Comparison Charts View
Side-by-side multi-design comparative cycle charts, speedup deltas, register churn comparison, and ranking table.
"""

from comparison_engine import compare_two_designs, rank_designs
import matplotlib.pyplot as plt
import pandas as pd


def render_design_comparison(st_module, loader):
    """Render Design Comparison Charts section."""
    st_module.header("6. Design Comparison & Ranking Charts")
    st_module.write("Compares multiple Hack CPU design variants across benchmark suites to evaluate cycle efficiency and rankings.")

    designs = loader.store.get_all_designs()
    if len(designs) < 2:
        st_module.info("At least two registered designs are required for side-by-side comparison.")
        return

    col1, col2 = st_module.columns(2)
    with col1:
        dA = st_module.selectbox("Select Design A:", [d["design_id"] for d in designs], index=0)
    with col2:
        dB = st_module.selectbox("Select Design B:", [d["design_id"] for d in designs], index=min(1, len(designs)-1))

    if dA == dB:
        st_module.warning("Select two different designs for comparative analysis.")
        return

    try:
        comp = compare_two_designs(loader.store, dA, dB)
        st_module.subheader(f"Comparison Summary: Design {dA} vs Design {dB}")
        st_module.write(f"**Structural State:** {comp['structural_difference']}")
        st_module.metric("Overall Speedup %", f"{comp['overall_speedup_pct']}%", f"{comp['total_cycles_b'] - comp['total_cycles_a']} cycles delta")

        b_comps = comp["benchmark_comparisons"]
        if b_comps:
            b_df = pd.DataFrame(b_comps)
            st_module.subheader("Per-Benchmark Comparative Performance")
            st_module.dataframe(b_df[["benchmark_id", "design_a_avg_cycles", "design_b_avg_cycles", "speedup_pct", "outputs_match"]])

            # Comparative Bar Chart
            fig, ax = plt.subplots(figsize=(8, 4))
            x_labels = b_df["benchmark_id"]
            x = range(len(x_labels))
            width = 0.35

            ax.bar([i - width/2 for i in x], b_df["design_a_avg_cycles"], width, label=f"Design {dA}", color="#3B82F6")
            ax.bar([i + width/2 for i in x], b_df["design_b_avg_cycles"], width, label=f"Design {dB}", color="#10B981")

            ax.set_ylabel("Average Execution Cycles")
            ax.set_title("Cycle Count Comparison per Benchmark")
            ax.set_xticks(x)
            ax.set_xticklabels(x_labels)
            ax.legend()
            ax.grid(True, linestyle="--", alpha=0.5)

            plt.tight_layout()
            st_module.pyplot(fig)
            plt.close(fig)

    except Exception as e:
        st_module.error(f"Error computing comparison: {e}")

    # Global Rankings
    st_module.subheader("Global HACK CPU Design Rankings")
    rankings = rank_designs(loader.store)
    if rankings:
        rank_df = pd.DataFrame(rankings)
        st_module.dataframe(rank_df[["rank", "design_id", "hdl_source_hash", "total_avg_cycles", "avg_register_churn_pct", "relative_cycle_overhead_pct"]])
