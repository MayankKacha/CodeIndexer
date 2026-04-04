import os
import sys
import logging
import asyncio
from code_indexer.pipeline.indexer import CodeIndexerPipeline
from code_indexer.evaluator.benchmarker import Benchmarker

logging.basicConfig(level=logging.INFO)

# Set DB type for CGC
os.environ["DATABASE_TYPE"] = "kuzudb"

pipeline = CodeIndexerPipeline()
benchmarker = Benchmarker(pipeline)

try:
    query = "evaluate_relevance"
    print(f"Running benchmark for: {query}")
    results = asyncio.run(benchmarker.run_benchmark(query))
    
    for arch, res in results.items():
        print(f"--- {arch} ---")
        print(f"Latency: {res.latency_ms:.2f}ms")
        print(f"Tokens: {res.token_count}")
        print(f"Score: {res.relevance_score}/10")
        if not res.context.strip():
            print("WARNING: Empty context!")
            
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
