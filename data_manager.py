"""Data Manager - Historical Trade Archive & Analytics

Handles:
  - Daily trade archiving
  - Monthly summaries
  - Historical queries
  - Automatic cleanup
  
Author: Code Puppy 🐶
Date: March 2026
"""

import json
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from collections import defaultdict


# ============================================================================
# PATHS
# ============================================================================

PROJECT_ROOT = Path(__file__).parent
TRADE_LOG_FILE = PROJECT_ROOT / "trade_log.json"
ARCHIVES_DIR = PROJECT_ROOT / "archives"
SUMMARIES_DIR = PROJECT_ROOT / "summaries"

# Create directories if they don't exist
ARCHIVES_DIR.mkdir(exist_ok=True)
SUMMARIES_DIR.mkdir(exist_ok=True)


# ============================================================================
# CORE ARCHIVE FUNCTIONS
# ============================================================================

def archive_today_trades(date: Optional[str] = None) -> Dict[str, Any]:
    """
    Archive today's trade_log.json to dated file.
    
    Args:
        date: YYYY-MM-DD format (default: today)
        
    Returns:
        {
            'success': bool,
            'archived_file': str,
            'trade_count': int,
            'message': str
        }
    """
    try:
        # Determine date
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        
        # Check if trade_log.json exists
        if not TRADE_LOG_FILE.exists():
            return {
                'success': False,
                'archived_file': None,
                'trade_count': 0,
                'message': 'No trade_log.json found to archive'
            }
        
        # Read current trade log
        with open(TRADE_LOG_FILE, 'r') as f:
            trade_data = json.load(f)
        
        # Create archive filename
        archive_file = ARCHIVES_DIR / f"trade_log_{date}.json"
        
        # Check if already archived
        if archive_file.exists():
            # Merge with existing
            with open(archive_file, 'r') as f:
                existing = json.load(f)
            
            # Merge trades (avoid duplicates by ID)
            existing_ids = {t['id'] for t in existing.get('trades', [])}
            new_trades = [t for t in trade_data.get('trades', []) if t['id'] not in existing_ids]
            
            if new_trades:
                existing['trades'].extend(new_trades)
                existing['orders_placed'] = len(existing['trades'])
                existing['total_pnl'] = sum(t.get('pnl', 0) for t in existing['trades'])
                trade_data = existing
        
        # Save archive
        with open(archive_file, 'w') as f:
            json.dump(trade_data, f, indent=2)
        
        trade_count = len(trade_data.get('trades', []))
        
        return {
            'success': True,
            'archived_file': str(archive_file),
            'trade_count': trade_count,
            'message': f'✅ Archived {trade_count} trades to {archive_file.name}'
        }
        
    except Exception as e:
        return {
            'success': False,
            'archived_file': None,
            'trade_count': 0,
            'message': f'❌ Archive failed: {e}'
        }


def cleanup_old_archives(keep_days: int = 90) -> Dict[str, Any]:
    """
    Delete archive files older than keep_days.
    
    Args:
        keep_days: Keep archives from last N days (default: 90)
        
    Returns:
        {
            'success': bool,
            'deleted_count': int,
            'deleted_files': List[str],
            'message': str
        }
    """
    try:
        cutoff_date = datetime.now() - timedelta(days=keep_days)
        deleted_files = []
        
        for archive_file in ARCHIVES_DIR.glob("trade_log_*.json"):
            # Extract date from filename: trade_log_2026-03-23.json
            try:
                date_str = archive_file.stem.replace('trade_log_', '')
                file_date = datetime.strptime(date_str, "%Y-%m-%d")
                
                if file_date < cutoff_date:
                    archive_file.unlink()
                    deleted_files.append(archive_file.name)
            except ValueError:
                # Skip files that don't match pattern
                continue
        
        return {
            'success': True,
            'deleted_count': len(deleted_files),
            'deleted_files': deleted_files,
            'message': f'🗑️ Deleted {len(deleted_files)} old archives (older than {keep_days} days)'
        }
        
    except Exception as e:
        return {
            'success': False,
            'deleted_count': 0,
            'deleted_files': [],
            'message': f'❌ Cleanup failed: {e}'
        }


# ============================================================================
# QUERY FUNCTIONS
# ============================================================================

def get_trades_for_date(date: str) -> Optional[Dict[str, Any]]:
    """
    Get all trades for a specific date.
    
    Args:
        date: YYYY-MM-DD format
        
    Returns:
        Trade data dict or None if not found
    """
    archive_file = ARCHIVES_DIR / f"trade_log_{date}.json"
    
    if not archive_file.exists():
        # Check if it's today's date (might be in trade_log.json)
        today = datetime.now().strftime("%Y-%m-%d")
        if date == today and TRADE_LOG_FILE.exists():
            with open(TRADE_LOG_FILE, 'r') as f:
                return json.load(f)
        return None
    
    with open(archive_file, 'r') as f:
        return json.load(f)


def get_date_range_trades(start_date: str, end_date: str) -> List[Dict[str, Any]]:
    """
    Get trades for a date range.
    
    Args:
        start_date: YYYY-MM-DD
        end_date: YYYY-MM-DD
        
    Returns:
        List of trade data dicts
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    
    all_trades = []
    current = start
    
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        day_data = get_trades_for_date(date_str)
        
        if day_data:
            all_trades.append(day_data)
        
        current += timedelta(days=1)
    
    return all_trades


def get_last_n_days_trades(days: int = 7) -> List[Dict[str, Any]]:
    """
    Get trades from last N days.
    
    Args:
        days: Number of days to look back
        
    Returns:
        List of trade data dicts
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days-1)
    
    return get_date_range_trades(
        start_date.strftime("%Y-%m-%d"),
        end_date.strftime("%Y-%m-%d")
    )


# ============================================================================
# STATISTICS FUNCTIONS
# ============================================================================

def calculate_stats(trades_list: List[Dict]) -> Dict[str, Any]:
    """
    Calculate statistics from a list of trades.
    
    Args:
        trades_list: List of trade dicts
        
    Returns:
        Statistics dict
    """
    if not trades_list:
        return {
            'total_trades': 0,
            'total_pnl': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate': 0,
            'avg_win': 0,
            'avg_loss': 0,
            'max_win': 0,
            'max_loss': 0,
            'profit_factor': 0
        }
    
    # Filter only exited trades
    exited = [t for t in trades_list if t.get('status') == 'exited']
    
    if not exited:
        return {
            'total_trades': len(trades_list),
            'total_pnl': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate': 0,
            'avg_win': 0,
            'avg_loss': 0,
            'max_win': 0,
            'max_loss': 0,
            'profit_factor': 0
        }
    
    # Calculate stats
    pnls = [t.get('pnl', 0) for t in exited]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    
    total_pnl = sum(pnls)
    total_wins = sum(wins)
    total_losses = abs(sum(losses))
    
    return {
        'total_trades': len(exited),
        'total_pnl': round(total_pnl, 2),
        'winning_trades': len(wins),
        'losing_trades': len(losses),
        'win_rate': round(len(wins) / len(exited) * 100, 2) if exited else 0,
        'avg_win': round(sum(wins) / len(wins), 2) if wins else 0,
        'avg_loss': round(sum(losses) / len(losses), 2) if losses else 0,
        'max_win': round(max(wins), 2) if wins else 0,
        'max_loss': round(min(losses), 2) if losses else 0,
        'profit_factor': round(total_wins / total_losses, 2) if total_losses > 0 else 0
    }


def get_last_n_days_stats(days: int = 7) -> Dict[str, Any]:
    """
    Get statistics for last N days.
    
    Args:
        days: Number of days
        
    Returns:
        Statistics dict
    """
    days_data = get_last_n_days_trades(days)
    
    # Combine all trades
    all_trades = []
    for day in days_data:
        all_trades.extend(day.get('trades', []))
    
    stats = calculate_stats(all_trades)
    stats['period'] = f'Last {days} days'
    stats['days_count'] = len(days_data)
    
    return stats


def get_monthly_stats(year: int, month: int) -> Dict[str, Any]:
    """
    Get statistics for a specific month.
    
    Args:
        year: 2026
        month: 1-12
        
    Returns:
        Monthly statistics dict
    """
    # Get date range for month
    start_date = datetime(year, month, 1)
    
    # Last day of month
    if month == 12:
        end_date = datetime(year + 1, 1, 1) - timedelta(days=1)
    else:
        end_date = datetime(year, month + 1, 1) - timedelta(days=1)
    
    # Get trades
    days_data = get_date_range_trades(
        start_date.strftime("%Y-%m-%d"),
        end_date.strftime("%Y-%m-%d")
    )
    
    # Combine all trades
    all_trades = []
    daily_pnls = []
    
    for day in days_data:
        trades = day.get('trades', [])
        all_trades.extend(trades)
        daily_pnls.append({
            'date': day['date'],
            'pnl': day.get('total_pnl', 0),
            'trades': len(trades)
        })
    
    # Calculate stats
    stats = calculate_stats(all_trades)
    stats['period'] = f'{start_date.strftime("%B %Y")}'
    stats['trading_days'] = len(days_data)
    
    # Best/worst days
    if daily_pnls:
        best_day = max(daily_pnls, key=lambda x: x['pnl'])
        worst_day = min(daily_pnls, key=lambda x: x['pnl'])
        
        stats['best_day'] = best_day['date']
        stats['best_day_pnl'] = round(best_day['pnl'], 2)
        stats['worst_day'] = worst_day['date']
        stats['worst_day_pnl'] = round(worst_day['pnl'], 2)
    
    return stats


def generate_monthly_summary(year: int, month: int) -> Dict[str, Any]:
    """
    Generate and save monthly summary report.
    
    Args:
        year: 2026
        month: 1-12
        
    Returns:
        Result dict with success status
    """
    try:
        stats = get_monthly_stats(year, month)
        
        # Save to summaries/
        summary_file = SUMMARIES_DIR / f"monthly_{year}-{month:02d}.json"
        
        with open(summary_file, 'w') as f:
            json.dump(stats, f, indent=2)
        
        return {
            'success': True,
            'summary_file': str(summary_file),
            'stats': stats,
            'message': f'✅ Generated summary for {stats["period"]}'
        }
        
    except Exception as e:
        return {
            'success': False,
            'summary_file': None,
            'stats': None,
            'message': f'❌ Summary generation failed: {e}'
        }


# ============================================================================
# STRATEGY PERFORMANCE
# ============================================================================

def get_strategy_performance(strategy: str, days: int = 30) -> Dict[str, Any]:
    """
    Get performance stats for a specific strategy.
    
    Args:
        strategy: 'smart_router', 'supertrend', etc.
        days: Look back period
        
    Returns:
        Strategy performance dict
    """
    days_data = get_last_n_days_trades(days)
    
    # Filter trades by strategy
    strategy_trades = []
    for day in days_data:
        for trade in day.get('trades', []):
            # Check if trade has strategy field
            if trade.get('strategy') == strategy:
                strategy_trades.append(trade)
    
    stats = calculate_stats(strategy_trades)
    stats['strategy'] = strategy
    stats['period'] = f'Last {days} days'
    
    return stats


# ============================================================================
# DATA MANAGER CLASS (Optional convenience wrapper)
# ============================================================================

class TradeDataManager:
    """
    Convenience wrapper for all data management functions.
    """
    
    def __init__(self):
        self.archives_dir = ARCHIVES_DIR
        self.summaries_dir = SUMMARIES_DIR
    
    # Archive functions
    def archive_today(self, date: Optional[str] = None):
        return archive_today_trades(date)
    
    def cleanup_old(self, keep_days: int = 90):
        return cleanup_old_archives(keep_days)
    
    # Query functions
    def get_date(self, date: str):
        return get_trades_for_date(date)
    
    def get_range(self, start_date: str, end_date: str):
        return get_date_range_trades(start_date, end_date)
    
    def get_last_days(self, days: int = 7):
        return get_last_n_days_trades(days)
    
    # Stats functions
    def last_n_days_stats(self, days: int = 7):
        return get_last_n_days_stats(days)
    
    def monthly_stats(self, year: int, month: int):
        return get_monthly_stats(year, month)
    
    def generate_monthly(self, year: int, month: int):
        return generate_monthly_summary(year, month)
    
    def strategy_performance(self, strategy: str, days: int = 30):
        return get_strategy_performance(strategy, days)


# ============================================================================
# AUTO-RUN ON IMPORT (Optional)
# ============================================================================

if __name__ == "__main__":
    # Test the functions
    dm = TradeDataManager()
    
    print("📊 Testing Data Manager...")
    print()
    
    # Test archive
    result = dm.archive_today()
    print(result['message'])
    
    # Test stats
    stats = dm.last_n_days_stats(7)
    print(f"\n📈 Last 7 days stats:")
    print(f"  Trades: {stats['total_trades']}")
    print(f"  Win Rate: {stats['win_rate']:.1f}%")
    print(f"  Total P&L: ₹{stats['total_pnl']}")
    
    print("\n✅ Data Manager ready!")
