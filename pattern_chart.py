"""Generate visual charts for detected chart patterns.

Creates candlestick charts with pattern annotations, key levels,
and visual highlights to help traders see the pattern formation.
"""

import io
import base64
import pandas as pd
import numpy as np
from datetime import datetime
from pattern_detector import PatternMatch

try:
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend for server use
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from matplotlib.dates import DateFormatter
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    print("⚠️  matplotlib not available - pattern charts disabled")


def generate_pattern_chart(
    df: pd.DataFrame,
    pattern: PatternMatch,
    lookback: int = 50,
) -> str | None:
    """Generate a candlestick chart visualizing the detected pattern.
    
    Args:
        df: OHLC dataframe with datetime index
        pattern: Detected pattern with key levels and indices
        lookback: Number of candles to show before pattern start
    
    Returns:
        Base64-encoded PNG image string, or None if generation fails
    """
    if not MATPLOTLIB_AVAILABLE:
        return None
    
    try:
        # Determine chart window
        end_idx = pattern.end_idx
        start_idx = max(0, pattern.start_idx - lookback)
        
        # Slice dataframe
        chart_df = df.iloc[start_idx:end_idx+1].copy()
        if chart_df.empty:
            return None
        
        # Create figure
        fig, ax = plt.subplots(figsize=(14, 8), facecolor='#0a0a0a')
        ax.set_facecolor('#0a0a0a')
        
        # Plot candlesticks
        _plot_candlesticks(ax, chart_df)
        
        # Highlight pattern region
        pattern_start_idx = pattern.start_idx - start_idx
        pattern_end_idx = pattern.end_idx - start_idx
        _highlight_pattern_region(ax, chart_df, pattern_start_idx, pattern_end_idx)
        
        # Draw key levels (support, resistance, neckline, etc.)
        _draw_key_levels(ax, chart_df, pattern)
        
        # Add pattern annotation
        _add_pattern_annotation(ax, chart_df, pattern, pattern_start_idx, pattern_end_idx)
        
        # Format axes
        _format_chart(ax, chart_df, pattern)
        
        # Convert to base64
        buf = io.BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format='png', facecolor='#0a0a0a', dpi=100, bbox_inches='tight')
        buf.seek(0)
        img_base64 = base64.b64encode(buf.read()).decode('utf-8')
        plt.close(fig)
        
        return img_base64
        
    except Exception as e:
        print(f"⚠️  Pattern chart generation failed: {e}")
        return None


def _plot_candlesticks(ax, df: pd.DataFrame):
    """Plot candlesticks on the chart."""
    times = np.arange(len(df))
    
    for i, (idx, row) in enumerate(df.iterrows()):
        open_price = row['open']
        high_price = row['high']
        low_price = row['low']
        close_price = row['close']
        
        # Candle color
        is_bullish = close_price >= open_price
        color = '#00ff00' if is_bullish else '#ff0000'
        edge_color = '#00aa00' if is_bullish else '#aa0000'
        
        # Body
        body_height = abs(close_price - open_price)
        body_bottom = min(open_price, close_price)
        
        if body_height > 0:
            rect = Rectangle(
                (times[i] - 0.4, body_bottom),
                0.8, body_height,
                facecolor=color,
                edgecolor=edge_color,
                linewidth=0.5,
                alpha=0.8
            )
            ax.add_patch(rect)
        else:
            # Doji - just a line
            ax.plot([times[i] - 0.4, times[i] + 0.4], [open_price, close_price],
                   color=color, linewidth=1.5, alpha=0.8)
        
        # Wicks
        ax.plot([times[i], times[i]], [low_price, high_price],
               color=edge_color, linewidth=0.8, alpha=0.6)


def _highlight_pattern_region(ax, df: pd.DataFrame, start_idx: int, end_idx: int):
    """Highlight the region where the pattern formed."""
    if start_idx < 0 or end_idx >= len(df):
        return
    
    # Semi-transparent yellow box
    y_min = df['low'].min() * 0.999
    y_max = df['high'].max() * 1.001
    
    rect = Rectangle(
        (start_idx - 0.5, y_min),
        end_idx - start_idx + 1,
        y_max - y_min,
        facecolor='#ffff00',
        alpha=0.08,
        linewidth=0
    )
    ax.add_patch(rect)


def _draw_key_levels(ax, df: pd.DataFrame, pattern: PatternMatch):
    """Draw support, resistance, neckline, and other key levels."""
    x_max = len(df) - 0.5
    levels = pattern.key_levels or {}
    start_offset = pattern.start_idx   # global start idx

    # ── Trend Structure: zigzag + LH/LL markers ───────────────────
    if pattern.pattern_type == "structure":
        is_down = pattern.bias == "bearish"

        # Convert global indices to local chart indices
        def _local(global_idx: int) -> int:
            return global_idx - start_offset

        if is_down:
            lh_idxs = [_local(i) for i in levels.get("lh_indices", [])]
            ll_idxs = [_local(i) for i in levels.get("ll_indices", [])]
            lh_vals = levels.get("lh_values", [])
            ll_vals = levels.get("ll_values", [])

            # Draw zigzag connecting pivots
            all_pts = sorted(
                [(i, v, "LH") for i, v in zip(lh_idxs, lh_vals)] +
                [(i, v, "LL") for i, v in zip(ll_idxs, ll_vals)]
            )
            if len(all_pts) >= 2:
                xs = [p[0] for p in all_pts]
                ys = [p[1] for p in all_pts]
                ax.plot(xs, ys, color='#ff6666', linewidth=1.5,
                        linestyle='--', alpha=0.7, zorder=3, label='Structure')

            # LH markers (red downward triangles)
            for xi, yv in zip(lh_idxs, lh_vals):
                if 0 <= xi < len(df):
                    ax.plot(xi, yv, marker='v', color='#ff2222', markersize=10,
                            zorder=5, markeredgecolor='white', markeredgewidth=0.8)
                    ax.annotate(f"LH\n₹{yv:.0f}", xy=(xi, yv),
                                xytext=(0, 12), textcoords='offset points',
                                color='#ff8888', fontsize=8, fontweight='bold',
                                ha='center', va='bottom',
                                bbox=dict(boxstyle='round,pad=0.2', fc='#1a0000',
                                          ec='#ff4444', lw=0.8, alpha=0.9))

            # LL markers (red upward triangles at low)
            for xi, yv in zip(ll_idxs, ll_vals):
                if 0 <= xi < len(df):
                    ax.plot(xi, yv, marker='^', color='#ff4444', markersize=9,
                            zorder=5, markeredgecolor='white', markeredgewidth=0.8)
                    ax.annotate(f"LL\n₹{yv:.0f}", xy=(xi, yv),
                                xytext=(0, -18), textcoords='offset points',
                                color='#ff8888', fontsize=8, fontweight='bold',
                                ha='center', va='top',
                                bbox=dict(boxstyle='round,pad=0.2', fc='#1a0000',
                                          ec='#ff4444', lw=0.8, alpha=0.9))

            # Dashed resistance at latest LH
            latest_lh = levels.get("latest_lh")
            if latest_lh:
                ax.axhline(y=latest_lh, color='#ff4444', linestyle='--',
                           linewidth=1.5, alpha=0.6)
                ax.text(x_max, latest_lh, f" Sell Zone ₹{latest_lh:.0f}",
                        color='#ff6666', fontsize=9, va='bottom', fontweight='bold')

            # Structure break level
            sb = levels.get("structure_break")
            if sb:
                ax.axhline(y=sb, color='#ffaa00', linestyle=':',
                           linewidth=1.2, alpha=0.5)
                ax.text(x_max, sb, f" Break ₹{sb:.0f}",
                        color='#ffaa00', fontsize=8, va='top')

        else:  # Uptrend
            hh_idxs = [_local(i) for i in levels.get("hh_indices", [])]
            hl_idxs = [_local(i) for i in levels.get("hl_indices", [])]
            hh_vals = levels.get("hh_values", [])
            hl_vals = levels.get("hl_values", [])

            all_pts = sorted(
                [(i, v, "HH") for i, v in zip(hh_idxs, hh_vals)] +
                [(i, v, "HL") for i, v in zip(hl_idxs, hl_vals)]
            )
            if len(all_pts) >= 2:
                xs = [p[0] for p in all_pts]
                ys = [p[1] for p in all_pts]
                ax.plot(xs, ys, color='#44ff88', linewidth=1.5,
                        linestyle='--', alpha=0.7, zorder=3)

            for xi, yv in zip(hh_idxs, hh_vals):
                if 0 <= xi < len(df):
                    ax.plot(xi, yv, marker='^', color='#22ff44', markersize=10,
                            zorder=5, markeredgecolor='white', markeredgewidth=0.8)
                    ax.annotate(f"HH\n₹{yv:.0f}", xy=(xi, yv),
                                xytext=(0, 12), textcoords='offset points',
                                color='#88ff99', fontsize=8, fontweight='bold',
                                ha='center', va='bottom',
                                bbox=dict(boxstyle='round,pad=0.2', fc='#001a00',
                                          ec='#44ff88', lw=0.8, alpha=0.9))

            for xi, yv in zip(hl_idxs, hl_vals):
                if 0 <= xi < len(df):
                    ax.plot(xi, yv, marker='v', color='#00cc44', markersize=9,
                            zorder=5, markeredgecolor='white', markeredgewidth=0.8)
                    ax.annotate(f"HL\n₹{yv:.0f}", xy=(xi, yv),
                                xytext=(0, -18), textcoords='offset points',
                                color='#88ff99', fontsize=8, fontweight='bold',
                                ha='center', va='top',
                                bbox=dict(boxstyle='round,pad=0.2', fc='#001a00',
                                          ec='#44ff88', lw=0.8, alpha=0.9))

            latest_hl = levels.get("latest_hl")
            if latest_hl:
                ax.axhline(y=latest_hl, color='#44ff88', linestyle='--',
                           linewidth=1.5, alpha=0.6)
                ax.text(x_max, latest_hl, f" Buy Zone ₹{latest_hl:.0f}",
                        color='#44ff88', fontsize=9, va='top', fontweight='bold')

            sb = levels.get("structure_break")
            if sb:
                ax.axhline(y=sb, color='#ffaa00', linestyle=':',
                           linewidth=1.2, alpha=0.5)
                ax.text(x_max, sb, f" Break ₹{sb:.0f}",
                        color='#ffaa00', fontsize=8, va='bottom')
        return  # structure done

    # ── Double Bottom: draw W-shape zigzag ────────────────────────
    if pattern.name in ("Double Bottom", "Double Top"):
        is_bottom = pattern.name == "Double Bottom"
        t1_idx = levels.get("t1_idx")
        t2_idx = levels.get("t2_idx")
        nl_idx = levels.get("neckline_idx")
        t1_val = levels.get("trough1") or levels.get("peak1")
        t2_val = levels.get("trough2") or levels.get("peak2")
        nl_val = levels.get("neckline")

        # Adjust indices relative to chart window
        chart_offset = pattern.start_idx
        def _cl(gi):
            return gi - chart_offset if gi is not None else None

        ct1, ct2, cnl = _cl(t1_idx), _cl(t2_idx), _cl(nl_idx)

        pts = [(ct1, t1_val, 'T1'), (cnl, nl_val, 'NK'), (ct2, t2_val, 'T2')]
        pts = [(x, y, l) for x, y, l in pts if x is not None and y is not None and 0 <= x < len(df)]

        if len(pts) >= 2:
            color = '#00ff88' if is_bottom else '#ff4444'
            ax.plot([p[0] for p in pts], [p[1] for p in pts],
                    color=color, linewidth=2.0, linestyle='-', alpha=0.8,
                    zorder=4, marker='o', markersize=7,
                    markerfacecolor=color, markeredgecolor='white',
                    markeredgewidth=1)

        label_map = {'T1': 'T1', 'NK': 'Neck', 'T2': 'T2'}
        color_map = {'T1': '#00ff88', 'NK': '#00ffff', 'T2': '#00ff88'} if is_bottom else {'T1': '#ff4444', 'NK': '#00ffff', 'T2': '#ff4444'}
        for xi, yi, lbl in pts:
            offset_y = -18 if is_bottom else 12
            ax.annotate(f"{label_map[lbl]}\n₹{yi:.0f}", xy=(xi, yi),
                        xytext=(0, offset_y), textcoords='offset points',
                        color=color_map[lbl], fontsize=8, fontweight='bold',
                        ha='center', va='top' if offset_y < 0 else 'bottom',
                        bbox=dict(boxstyle='round,pad=0.25',
                                  fc='#0a1a12' if is_bottom else '#1a0a0a',
                                  ec=color_map[lbl], lw=0.8, alpha=0.9))

    # ── Generic key levels (for all other patterns) ────────────────
    x_min = -0.5

    if 'support' in levels and levels['support']:
        ax.axhline(y=levels['support'], color='#00ff00', linestyle='--',
                  linewidth=1.5, alpha=0.7, label='Support')
        ax.text(x_max, levels['support'], f" Support ₹{levels['support']:.0f}",
               color='#00ff00', fontsize=9, va='bottom', weight='bold')

    if 'resistance' in levels and levels['resistance']:
        ax.axhline(y=levels['resistance'], color='#ff0000', linestyle='--',
                  linewidth=1.5, alpha=0.7, label='Resistance')
        ax.text(x_max, levels['resistance'], f" Resistance ₹{levels['resistance']:.0f}",
               color='#ff0000', fontsize=9, va='top', weight='bold')

    if 'neckline' in levels and levels['neckline']:
        ax.axhline(y=levels['neckline'], color='#00ffff', linestyle='-',
                  linewidth=2, alpha=0.8, label='Neckline')
        ax.text(x_max, levels['neckline'], f" Neckline ₹{levels['neckline']:.0f}",
               color='#00ffff', fontsize=10, va='center', weight='bold')

    if 'entry' in levels and levels['entry']:
        ax.axhline(y=levels['entry'], color='#ffff00', linestyle=':',
                  linewidth=2, alpha=0.9, label='Entry')
        ax.text(x_max, levels['entry'], f" Entry ₹{levels['entry']:.0f}",
               color='#ffff00', fontsize=10, va='center', weight='bold')

    for i, target_key in enumerate(['target1', 'target2', 'target3']):
        if target_key in levels and levels[target_key]:
            ax.axhline(y=levels[target_key], color='#00ff88', linestyle=':',
                      linewidth=1.5, alpha=0.6)
            ax.text(x_max, levels[target_key], f" T{i+1} ₹{levels[target_key]:.0f}",
                   color='#00ff88', fontsize=8, va='center')

    if 'stop_loss' in levels and levels['stop_loss']:
        ax.axhline(y=levels['stop_loss'], color='#ff6666', linestyle=':',
                  linewidth=2, alpha=0.8, label='Stop Loss')
        ax.text(x_max, levels['stop_loss'], f" SL ₹{levels['stop_loss']:.0f}",
               color='#ff6666', fontsize=9, va='center', weight='bold')


def _add_pattern_annotation(ax, df: pd.DataFrame, pattern: PatternMatch,
                            start_idx: int, end_idx: int):
    """Add text annotation showing pattern details."""
    # Pattern name and confidence at top
    bias_color = '#00ff00' if pattern.bias == 'bullish' else '#ff0000' if pattern.bias == 'bearish' else '#ffff00'
    bias_emoji = '📈' if pattern.bias == 'bullish' else '📉' if pattern.bias == 'bearish' else '↔️'
    
    title_text = f"{bias_emoji} {pattern.name.upper()} ({pattern.confidence*100:.0f}% confidence)"
    
    ax.text(0.5, 0.98, title_text,
           transform=ax.transAxes,
           fontsize=14,
           weight='bold',
           color=bias_color,
           ha='center',
           va='top',
           bbox=dict(boxstyle='round,pad=0.5', facecolor='#1a1a1a', edgecolor=bias_color, linewidth=2))
    
    # Description at bottom
    if pattern.description:
        ax.text(0.5, 0.02, pattern.description,
               transform=ax.transAxes,
               fontsize=10,
               color='#cccccc',
               ha='center',
               va='bottom',
               bbox=dict(boxstyle='round,pad=0.4', facecolor='#1a1a1a', alpha=0.8))


def _format_chart(ax, df: pd.DataFrame, pattern: PatternMatch):
    """Format chart axes, labels, and styling."""
    # Grid
    ax.grid(True, color='#333333', linestyle=':', linewidth=0.5, alpha=0.5)
    
    # X-axis: show times for key candles
    time_labels = []
    time_positions = []
    
    # Show every 10th candle or so
    step = max(1, len(df) // 10)
    for i in range(0, len(df), step):
        if i < len(df):
            time_labels.append(df.index[i].strftime('%H:%M'))
            time_positions.append(i)
    
    ax.set_xticks(time_positions)
    ax.set_xticklabels(time_labels, rotation=45, ha='right', color='#cccccc')
    
    # Y-axis
    ax.tick_params(axis='y', colors='#cccccc')
    ax.set_ylabel('Price (₹)', color='#cccccc', fontsize=11, weight='bold')
    ax.set_xlabel(f'Time ({pattern.timeframe or "5m"})', color='#cccccc', fontsize=11, weight='bold')
    
    # Spines
    for spine in ax.spines.values():
        spine.set_edgecolor('#444444')
        spine.set_linewidth(1.5)
    
    # Tight layout
    ax.set_xlim(-0.5, len(df) - 0.5)
    y_min = df['low'].min() * 0.998
    y_max = df['high'].max() * 1.002
    ax.set_ylim(y_min, y_max)


def generate_pattern_chart_html(pattern_img_base64: str, pattern: PatternMatch) -> str:
    """Generate HTML snippet to embed the pattern chart in the UI.
    
    Args:
        pattern_img_base64: Base64-encoded PNG image
        pattern: Pattern details for metadata
    
    Returns:
        HTML string with embedded image
    """
    bias_color = '#00ff00' if pattern.bias == 'bullish' else '#ff0000' if pattern.bias == 'bearish' else '#ffc220'
    
    return f"""
    <div class="pattern-chart-container" style="
        background: #0a0a0a;
        border: 2px solid {bias_color};
        border-radius: 8px;
        padding: 16px;
        margin: 16px 0;
    ">
        <img src="data:image/png;base64,{pattern_img_base64}" 
             style="width: 100%; height: auto; border-radius: 4px;"
             alt="{pattern.name} Pattern Chart" />
    </div>
    """