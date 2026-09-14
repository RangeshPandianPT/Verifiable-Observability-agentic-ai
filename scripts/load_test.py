"""
Scale Argument Prototyping (Phase 6: Scaling & Evaluation).

Performs load testing on the synchronous check path (CCM)
to provide extrapolated latency/throughput numbers.
"""

import time
import statistics
import logging
from concurrent.futures import ThreadPoolExecutor

from verifiable_observability.core.constraint_monitor import build_ccm
from verifiable_observability.storage.models import Action, Trajectory, Task, Domain, StrategyProfile, RiskTier

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def generate_dummy_trajectory() -> Trajectory:
    task = Task(domain=Domain.FINANCE, description="Load test task")
    profile = StrategyProfile(
        task_id=task.task_id,
        domain=task.domain,
        task_type="transfer",
        risk_tier=RiskTier.MEDIUM,
        active_constraint_set_id="finance",
        expected_turn_range=(1, 5)
    )
    return Trajectory(task=task, strategy_profile=profile)


def run_load_test(num_calls: int = 10000, concurrency: int = 10):
    ccm = build_ccm("finance")
    trajectory = generate_dummy_trajectory()
    
    # Pre-allocate actions to measure only checking time
    actions = [
        Action(tool_name="execute_transfer", parameters={"amount_usd": 1000})
        for _ in range(num_calls)
    ]
    
    latencies = []
    
    def check_action(action: Action, seq_no: int):
        start = time.perf_counter()
        ccm.check(action, trajectory, sequence_no=seq_no)
        end = time.perf_counter()
        return (end - start) * 1000  # ms
        
    logger.info(f"Starting load test with {num_calls} calls, concurrency {concurrency}...")
    
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(check_action, actions[i], i)
            for i in range(num_calls)
        ]
        
        for future in futures:
            latencies.append(future.result())
            
    total_time = time.time() - start_time
    
    logger.info("--- Load Test Results ---")
    logger.info(f"Total Calls: {num_calls}")
    logger.info(f"Concurrency: {concurrency}")
    logger.info(f"Total Time: {total_time:.2f} seconds")
    logger.info(f"Throughput: {num_calls / total_time:.2f} calls/sec")
    
    logger.info(f"Average Latency: {statistics.mean(latencies):.3f} ms")
    logger.info(f"Median Latency: {statistics.median(latencies):.3f} ms")
    logger.info(f"p95 Latency: {statistics.quantiles(latencies, n=100)[94]:.3f} ms")
    logger.info(f"p99 Latency: {statistics.quantiles(latencies, n=100)[98]:.3f} ms")


if __name__ == "__main__":
    # In a real run we might do 10k - 100k
    run_load_test(num_calls=10000, concurrency=20)
