"""
Example: Compare OpenAI-style vs Anthropic-style Dreaming.

This example demonstrates:
1. How each architecture processes memory consolidation
2. The key differences (in-place rewrite vs parallel artifact)
3. Evaluation of both approaches
4. Rollback capability (Anthropic only)
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from typing import List, Dict, Any

from core.memory.data_structures import (
    Session, MemoryStore, MemoryType,
    create_session_from_transcript, create_memory_entry
)
from core.consolidation.openai_style import OpenAIDreaming
from core.consolidation.anthropic_style import AnthropicDreams
from core.evaluation.evaluator import (
    MemoryEvaluator, ZhangBenchmarkEvaluator,
    create_example_test_cases, create_example_zhang_test_data
)


def print_header(title: str):
    """Print a formatted header"""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def print_section(title: str):
    """Print a section header"""
    print(f"\n{'-' * 60}")
    print(f"  {title}")
    print(f"{'-' * 60}")


def create_sample_sessions() -> List[Session]:
    """Create sample session transcripts"""
    sessions = []

    # Session 1: Initial trip planning
    session1 = create_session_from_transcript(
        session_id="session_001",
        transcript=[
            ("user", "Help me plan a trip to Singapore in July"),
            ("assistant", "I'll help you plan your Singapore trip for July."),
            ("user", "I prefer hotels with strong AC and quiet dinners"),
            ("assistant", "Noted! I'll prioritize hotels with strong AC and quiet dining options."),
            ("user", "I'm vegetarian"),
            ("assistant", "I'll make sure all restaurant recommendations are vegetarian-friendly.")
        ]
    )
    sessions.append(session1)

    # Session 2: Follow-up with more preferences
    session2 = create_session_from_transcript(
        session_id="session_002",
        transcript=[
            ("user", "I also prefer wildlife photography opportunities"),
            ("assistant", "Great! I'll include wildlife photography spots in your itinerary."),
            ("user", "I don't like crowded bars, prefer quiet cafes"),
            ("assistant", "Understood. I'll focus on quiet cafes and avoid crowded bars."),
            ("user", "My work hotel is in Marina Bay area"),
            ("assistant", "Got it. I'll plan activities near Marina Bay for convenience.")
        ]
    )
    sessions.append(session2)

    # Session 3: Additional context
    session3 = create_session_from_transcript(
        session_id="session_003",
        transcript=[
            ("user", "I'll be there for 3 days after work"),
            ("assistant", "Perfect. I'll create a 3-day itinerary."),
            ("user", "I want to visit Gardens by the Bay and Night Safari"),
            ("assistant", "Excellent choices! I'll include both in your itinerary."),
            ("user", "I need strong AC in my room for sleeping"),
            ("assistant", "I'll make sure to prioritize hotels with excellent AC.")
        ]
    )
    sessions.append(session3)

    return sessions


def create_initial_memory_store() -> MemoryStore:
    """Create an initial memory store with some pre-existing memories"""
    store = MemoryStore(
        id="memory_store_initial",
        created_at=datetime.now(),
        updated_at=datetime.now()
    )

    # Add some pre-existing memories
    store.add_entry(create_memory_entry(
        entry_id="mem_001",
        content="User is planning a trip to Singapore",
        memory_type=MemoryType.FACTUAL,
        importance=0.7,
        confidence=0.8
    ))

    store.add_entry(create_memory_entry(
        entry_id="mem_002",
        content="User is vegetarian",
        memory_type=MemoryType.FACTUAL,
        importance=0.9,
        confidence=0.95
    ))

    return store


def demonstrate_openai_style():
    """Demonstrate OpenAI-style dreaming"""
    print_header("OpenAI-Style Dreaming (Rewrite in Place)")

    print("\nArchitecture Characteristics:")
    print("  • Rewrites memory state continuously")
    print("  • Each pass builds on previous output")
    print("  • No rollback capability")
    print("  • Risk of degradation from iterated consolidation")

    # Create initial store
    initial_store = create_initial_memory_store()
    print(f"\nInitial memory store:")
    print(f"  ID: {initial_store.id}")
    print(f"  Size: {initial_store.size} entries")
    print(f"  Total importance: {initial_store.total_importance:.2f}")

    # Create dreamer
    dreamer = OpenAIDreaming(initial_store)

    # Create sample sessions
    sessions = create_sample_sessions()
    print(f"\nProcessing {len(sessions)} sessions...")

    # Execute dream
    result = dreamer.dream(sessions)

    print(f"\nDreaming complete!")
    print(f"  Sessions processed: {result.num_sessions_processed}")
    print(f"  Entries added: {result.num_entries_added}")
    print(f"  Entries updated: {result.num_entries_updated}")
    print(f"  Entries removed: {result.num_entries_removed}")
    print(f"  Duration: {result.duration_seconds:.3f} seconds")

    # Show final state
    print(f"\nFinal memory store (rewritten in place):")
    print(f"  ID: {dreamer.memory_store.id}")
    print(f"  Size: {dreamer.memory_store.size} entries")
    print(f"  Total importance: {dreamer.memory_store.total_importance:.2f}")

    print(f"\nMemory entries:")
    for entry in dreamer.memory_store.entries:
        print(f"  [{entry.memory_type.value}] {entry.content}")
        print(f"    Importance: {entry.importance:.2f}, Confidence: {entry.confidence:.2f}")

    return dreamer.memory_store


def demonstrate_anthropic_style():
    """Demonstrate Anthropic-style dreaming"""
    print_header("Anthropic-Style Dreams (Parallel Artifact)")

    print("\nArchitecture Characteristics:")
    print("  • Input store is NEVER modified")
    print("  • Produces separate, reorganized memory store")
    print("  • Allows comparison between pre-dream and post-dream")
    print("  • Supports rollback if dream degrades performance")

    # Create initial store
    initial_store = create_initial_memory_store()
    print(f"\nInitial memory store:")
    print(f"  ID: {initial_store.id}")
    print(f"  Size: {initial_store.size} entries")
    print(f"  Total importance: {initial_store.total_importance:.2f}")

    # Create dreamer
    dreamer = AnthropicDreams(initial_store)

    # Create sample sessions
    sessions = create_sample_sessions()
    print(f"\nProcessing {len(sessions)} sessions...")

    # Execute dream
    new_store, result = dreamer.dream(sessions)

    print(f"\nDreaming complete!")
    print(f"  Sessions processed: {result.num_sessions_processed}")
    print(f"  Entries added: {result.num_entries_added}")
    print(f"  Entries updated: {result.num_entries_updated}")
    print(f"  Entries removed: {result.num_entries_removed}")
    print(f"  Duration: {result.duration_seconds:.3f} seconds")

    # Show both stores
    print(f"\nInput store (preserved):")
    print(f"  ID: {dreamer.input_store.id}")
    print(f"  Size: {dreamer.input_store.size} entries")

    print(f"\nOutput store (new artifact):")
    print(f"  ID: {new_store.id}")
    print(f"  Size: {new_store.size} entries")
    print(f"  Total importance: {new_store.total_importance:.2f}")

    print(f"\nMemory entries in new store:")
    for entry in new_store.entries:
        print(f"  [{entry.memory_type.value}] {entry.content}")
        print(f"    Importance: {entry.importance:.2f}, Confidence: {entry.confidence:.2f}")

    # Demonstrate rollback capability
    print(f"\n{'-' * 60}")
    print("  Rollback Capability (Anthropic Only)")
    print(f"{'-' * 60}")

    # Evaluate if dream helped
    test_cases = create_example_test_cases()
    evaluation = dreamer.evaluate_dream(new_store, lambda store: 0.8)  # Simplified evaluation

    print(f"\nDream evaluation:")
    print(f"  Input score: {evaluation['input_score']:.2f}")
    print(f"  New score: {evaluation['new_score']:.2f}")
    print(f"  Improvement: {evaluation['improvement']:.2f}")
    print(f"  Helped: {evaluation['helped']}")
    print(f"  Recommendation: {evaluation['recommendation']}")

    # Show rollback
    if evaluation['recommendation'] == 'reject':
        print(f"\n  Since dream didn't help, we can rollback!")
        previous_store = dreamer.rollback_to_previous()
        if previous_store:
            print(f"  Rolled back to: {previous_store.id}")
        else:
            print(f"  This is the first dream, no previous store to rollback to")
            print(f"  But the input store is still preserved!")
    else:
        print(f"\n  Dream helped! Keeping the new store.")

    return dreamer.input_store, new_store


def compare_architectures():
    """Compare both architectures side by side"""
    print_header("Architecture Comparison")

    # Run both demonstrations
    openai_store = demonstrate_openai_style()
    anthropic_input, anthropic_output = demonstrate_anthropic_style()

    print_header("Key Differences")

    print("\n1. Memory Store Handling:")
    print("   OpenAI: Rewrites in place (same store ID)")
    print("   Anthropic: Produces new store (different store ID)")

    print("\n2. Rollback Capability:")
    print("   OpenAI: No rollback (previous state overwritten)")
    print("   Anthropic: Full rollback (input store preserved)")

    print("\n3. Evaluation:")
    print("   OpenAI: Must evaluate after the fact")
    print("   Anthropic: Can compare pre-dream vs post-dream")

    print("\n4. Risk Profile:")
    print("   OpenAI: Higher risk (iterated consolidation)")
    print("   Anthropic: Lower risk (reversible decisions)")

    print("\n5. Use Case:")
    print("   OpenAI: When you trust the consolidation process")
    print("   Anthropic: When you need to verify and potentially rollback")


def run_zhang_benchmark():
    """Run Zhang's benchmark tests to check for failure modes"""
    print_header("Zhang Benchmark Tests")

    print("\nTesting for the three failure modes identified in Zhang's research:")
    print("  1. Misgrouping: Distinct episodes merge into single entries")
    print("  2. Interference: Abstraction strips applicability conditions")
    print("  3. Overfit: Specific selectors get deleted as distribution narrows")

    # Create a memory store with some consolidated memories
    store = MemoryStore(
        id="memory_store_zhang_test",
        created_at=datetime.now(),
        updated_at=datetime.now()
    )

    # Add some memories that might show failure modes
    store.add_entry(create_memory_entry(
        entry_id="mem_001",
        content="User asked about Python and JavaScript programming",
        memory_type=MemoryType.ABSTRACT,
        importance=0.6,
        confidence=0.7
    ))

    store.add_entry(create_memory_entry(
        entry_id="mem_002",
        content="User prefers vegetarian food and quiet places",
        memory_type=MemoryType.PREFERENCES,
        importance=0.8,
        confidence=0.9
    ))

    # Run Zhang benchmark
    zhang_evaluator = ZhangBenchmarkEvaluator()
    test_data = create_example_zhang_test_data()

    print(f"\nTest data:")
    print(f"  Distinct episodes: {len(test_data['distinct_episodes'])}")
    print(f"  Context-specific lessons: {len(test_data['context_specific_lessons'])}")
    print(f"  Specific selectors: {len(test_data['specific_selectors'])}")

    results = zhang_evaluator.run_all_tests(store, test_data)

    print(f"\nResults:")
    for test_name, result in results.items():
        if test_name == "overall_passed":
            continue
        print(f"\n  {test_name.upper()}:")
        print(f"    Passed: {result['passed']}")
        print(f"    Details: {result['details']}")

    print(f"\n  Overall passed: {results['overall_passed']}")


def main():
    """Run the complete example"""
    print("=" * 80)
    print("  Dreaming in AI: Architecture Comparison Example")
    print("=" * 80)

    # Compare architectures
    compare_architectures()

    # Run Zhang benchmark
    run_zhang_benchmark()

    print_header("Summary")
    print("\nThis example demonstrates the two main approaches to dreaming in AI:")
    print("\n1. OpenAI-style (Dreaming V3):")
    print("   • Rewrites memory in place")
    print("   • More aggressive, riskier")
    print("   • No rollback capability")
    print("   • Better for: When you trust the process")

    print("\n2. Anthropic-style (Dreams):")
    print("   • Preserves input, produces parallel artifact")
    print("   • More cautious, safer")
    print("   • Full rollback capability")
    print("   • Better for: When you need to verify and potentially rollback")

    print("\nBoth approaches aim to:")
    print("  • Learn from experience (session transcripts)")
    print("  • Improve accuracy over time")
    print("  • Stay current as information changes")
    print("  • Build personalization without manual intervention")

    print("\nFor more information:")
    print("  • Read DREAMING_IN_AI.md for the complete concept")
    print("  • Check core/consolidation/ for implementation details")
    print("  • See core/evaluation/ for testing frameworks")


if __name__ == "__main__":
    main()