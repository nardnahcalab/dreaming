"""
Evaluation framework for comparing memory consolidation architectures.

This module provides tools to:
1. Evaluate memory store quality
2. Compare pre-dream vs post-dream performance
3. Test different consolidation strategies
4. Measure key metrics from Zhang's research
"""

from datetime import datetime
from typing import List, Dict, Any, Callable, Optional
import json

from ..memory.data_structures import (
    Session, MemoryEntry, MemoryStore, MemoryType
)


class MemoryEvaluator:
    """
    Evaluates memory store quality and performance.
    
    Based on the research from Zhang's paper and the Arize article,
    we need to measure:
    1. Context carry-forward: Does the AI remember relevant information?
    2. Preference following: Does it respect user constraints?
    3. Temporal accuracy: Does it update stale information?
    4. Performance: Does it answer better with memory vs without?
    """

    def __init__(self):
        self.evaluation_history: List[Dict[str, Any]] = []

    def evaluate_memory_store(
        self,
        store: MemoryStore,
        test_cases: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Evaluate a memory store against test cases.

        Args:
            store: Memory store to evaluate
            test_cases: List of test cases, each with:
                - question: The question to answer
                - expected_keywords: Keywords that should appear in answer
                - context_relevance: How relevant the memory is (0.0 to 1.0)
                - preference_type: Type of preference being tested

        Returns:
            Evaluation results
        """
        results = {
            "store_id": store.id,
            "timestamp": datetime.now(),
            "total_tests": len(test_cases),
            "passed": 0,
            "failed": 0,
            "scores": {},
            "details": []
        }

        for i, test_case in enumerate(test_cases):
            test_result = self._run_single_test(store, test_case, i)
            results["details"].append(test_result)

            if test_result["passed"]:
                results["passed"] += 1
            else:
                results["failed"] += 1

            # Track by test type
            test_type = test_case.get("test_type", "general")
            if test_type not in results["scores"]:
                results["scores"][test_type] = {"passed": 0, "total": 0}
            results["scores"][test_type]["total"] += 1
            if test_result["passed"]:
                results["scores"][test_type]["passed"] += 1

        # Calculate overall score
        results["accuracy"] = results["passed"] / results["total_tests"] if results["total_tests"] > 0 else 0

        # Calculate scores by type
        for test_type, scores in results["scores"].items():
            scores["accuracy"] = scores["passed"] / scores["total"] if scores["total"] > 0 else 0

        self.evaluation_history.append(results)
        return results

    def _run_single_test(
        self,
        store: MemoryStore,
        test_case: Dict[str, Any],
        test_index: int
    ) -> Dict[str, Any]:
        """Run a single test case"""
        question = test_case.get("question", "")
        expected_keywords = test_case.get("expected_keywords", [])

        # Find relevant memories for this question
        relevant_memories = self._find_relevant_memories(store, question)

        # Check if expected keywords appear in relevant memories
        all_memory_text = " ".join([m.content for m in relevant_memories]).lower()
        
        keywords_found = 0
        missing_keywords = []
        
        for keyword in expected_keywords:
            if keyword.lower() in all_memory_text:
                keywords_found += 1
            else:
                missing_keywords.append(keyword)

        passed = keywords_found >= len(expected_keywords) * 0.5  # At least 50% of keywords

        return {
            "test_index": test_index,
            "question": question,
            "expected_keywords": expected_keywords,
            "found_keywords": [k for k in expected_keywords if k.lower() in all_memory_text],
            "missing_keywords": missing_keywords,
            "num_relevant_memories": len(relevant_memories),
            "passed": passed,
            "score": keywords_found / len(expected_keywords) if expected_keywords else 0
        }

    def _find_relevant_memories(
        self,
        store: MemoryStore,
        question: str
    ) -> List[MemoryEntry]:
        """Find memories relevant to a question"""
        question_words = set(question.lower().split())
        relevant = []

        for entry in store.entries:
            entry_words = set(entry.content.lower().split())
            # Calculate word overlap
            overlap = len(question_words & entry_words)
            if overlap > 0 or entry.importance > 0.7:
                relevant.append(entry)

        # Sort by relevance (word overlap + importance)
        relevant.sort(
            key=lambda e: len(question_words & set(e.content.lower().split())) + e.importance,
            reverse=True
        )

        return relevant[:5]  # Return top 5

    def compare_stores(
        self,
        store1: MemoryStore,
        store2: MemoryStore,
        test_cases: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Compare two memory stores.

        Args:
            store1: First store (e.g., pre-dream)
            store2: Second store (e.g., post-dream)
            test_cases: Test cases to evaluate both stores

        Returns:
            Comparison results
        """
        results1 = self.evaluate_memory_store(store1, test_cases)
        results2 = self.evaluate_memory_store(store2, test_cases)

        return {
            "store1": {
                "id": store1.id,
                "accuracy": results1["accuracy"],
                "scores": results1["scores"]
            },
            "store2": {
                "id": store2.id,
                "accuracy": results2["accuracy"],
                "scores": results2["scores"]
            },
            "difference": {
                "accuracy": results2["accuracy"] - results1["accuracy"],
                "improved": results2["accuracy"] > results1["accuracy"],
                "score_differences": {
                    test_type: results2["scores"].get(test_type, {}).get("accuracy", 0) -
                               results1["scores"].get(test_type, {}).get("accuracy", 0)
                    for test_type in set(list(results1["scores"].keys()) + list(results2["scores"].keys()))
                }
            }
        }

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all evaluations"""
        if not self.evaluation_history:
            return {"message": "No evaluations yet"}

        total_tests = sum(r["total_tests"] for r in self.evaluation_history)
        total_passed = sum(r["passed"] for r in self.evaluation_history)

        return {
            "total_evaluations": len(self.evaluation_history),
            "total_tests": total_tests,
            "total_passed": total_passed,
            "overall_accuracy": total_passed / total_tests if total_tests > 0 else 0,
            "recent_evaluations": self.evaluation_history[-5:]  # Last 5
        }


class ZhangBenchmarkEvaluator:
    """
    Evaluator based on Zhang's research paper.
    
    Tests for the specific failure modes identified:
    1. Misgrouping: Distinct episodes merge into single entries
    2. Interference: Abstraction strips applicability conditions
    3. Overfit: Specific selectors get deleted as distribution narrows
    """

    def __init__(self):
        self.results: List[Dict[str, Any]] = []

    def test_misgrouping(
        self,
        store: MemoryStore,
        distinct_episodes: List[str]
    ) -> Dict[str, Any]:
        """
        Test if distinct episodes are being merged incorrectly.

        Args:
            store: Memory store to test
            distinct_episodes: List of distinct episodes that should remain separate

        Returns:
            Test results
        """
        # Check if distinct episodes are still distinguishable
        episode_memories = []
        for episode in distinct_episodes:
            # Find memories related to this episode
            related = [
                m for m in store.entries
                if self._is_related(m.content, episode)
            ]
            episode_memories.append(related)

        # Check if episodes are merged (have overlapping memories)
        merged_count = 0
        for i in range(len(episode_memories)):
            for j in range(i + 1, len(episode_memories)):
                # Check if memories overlap significantly
                if self._memories_overlap(episode_memories[i], episode_memories[j]):
                    merged_count += 1

        return {
            "test": "misgrouping",
            "num_episodes": len(distinct_episodes),
            "merged_pairs": merged_count,
            "passed": merged_count == 0,
            "details": f"{merged_count} pairs of distinct episodes were merged"
        }

    def test_interference(
        self,
        store: MemoryStore,
        context_specific_lessons: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """
        Test if abstraction strips applicability conditions.

        Args:
            store: Memory store to test
            context_specific_lessons: List of lessons with specific contexts

        Returns:
            Test results
        """
        overgeneralized = 0
        for lesson in context_specific_lessons:
            context = lesson.get("context", "")
            lesson_text = lesson.get("lesson", "")

            # Check if the lesson is still associated with its context
            found_with_context = False
            for memory in store.entries:
                if self._is_related(memory.content, lesson_text):
                    if context.lower() in memory.content.lower():
                        found_with_context = True
                        break

            if not found_with_context:
                overgeneralized += 1

        return {
            "test": "interference",
            "total_lessons": len(context_specific_lessons),
            "overgeneralized": overgeneralized,
            "passed": overgeneralized == 0,
            "details": f"{overgeneralized} lessons lost their applicability conditions"
        }

    def test_overfit(
        self,
        store: MemoryStore,
        specific_selectors: List[str]
    ) -> Dict[str, Any]:
        """
        Test if specific selectors are being deleted.

        Args:
            store: Memory store to test
            specific_selectors: List of specific selectors that should be preserved

        Returns:
            Test results
        """
        lost_selectors = []
        for selector in specific_selectors:
            found = False
            for memory in store.entries:
                if selector.lower() in memory.content.lower():
                    found = True
                    break
            if not found:
                lost_selectors.append(selector)

        return {
            "test": "overfit",
            "total_selectors": len(specific_selectors),
            "lost": len(lost_selectors),
            "lost_selectors": lost_selectors,
            "passed": len(lost_selectors) == 0,
            "details": f"{len(lost_selectors)} specific selectors were lost"
        }

    def run_all_tests(
        self,
        store: MemoryStore,
        test_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run all Zhang benchmark tests"""
        results = {}

        if "distinct_episodes" in test_data:
            results["misgrouping"] = self.test_misgrouping(
                store, test_data["distinct_episodes"]
            )

        if "context_specific_lessons" in test_data:
            results["interference"] = self.test_interference(
                store, test_data["context_specific_lessons"]
            )

        if "specific_selectors" in test_data:
            results["overfit"] = self.test_overfit(
                store, test_data["specific_selectors"]
            )

        # Calculate overall pass rate
        all_passed = all(r.get("passed", True) for r in results.values())
        results["overall_passed"] = all_passed

        self.results.append(results)
        return results

    def _is_related(self, text1: str, text2: str) -> bool:
        """Check if two texts are related"""
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        if not words1 or not words2:
            return False
        overlap = len(words1 & words2) / min(len(words1), len(words2))
        return overlap > 0.3

    def _memories_overlap(self, mems1: List, mems2: List) -> bool:
        """Check if two sets of memories overlap significantly"""
        if not mems1 or not mems2:
            return False

        # Check if any memory in set1 is similar to any in set2
        for m1 in mems1:
            for m2 in mems2:
                if self._is_related(m1.content, m2.content):
                    return True
        return False


# Example test cases
def create_example_test_cases() -> List[Dict[str, Any]]:
    """Create example test cases for evaluation"""
    return [
        {
            "test_type": "context_carryforward",
            "question": "What is the user's dietary preference?",
            "expected_keywords": ["vegetarian"],
            "context_relevance": 1.0
        },
        {
            "test_type": "context_carryforward",
            "question": "What hotel features does the user prefer?",
            "expected_keywords": ["strong", "ac", "quiet"],
            "context_relevance": 0.9
        },
        {
            "test_type": "temporal_accuracy",
            "question": "Is the user planning a trip to Singapore?",
            "expected_keywords": ["singapore"],
            "context_relevance": 0.8
        },
        {
            "test_type": "preference_following",
            "question": "Recommend restaurants for the user",
            "expected_keywords": ["vegetarian"],
            "context_relevance": 1.0
        }
    ]


# Example Zhang benchmark test data
def create_example_zhang_test_data() -> Dict[str, Any]:
    """Create example test data for Zhang benchmark"""
    return {
        "distinct_episodes": [
            "User asked about Python programming",
            "User asked about JavaScript programming",
            "User asked about cooking recipes"
        ],
        "context_specific_lessons": [
            {
                "context": "when using Python",
                "lesson": "use list comprehensions for filtering"
            },
            {
                "context": "when using JavaScript",
                "lesson": "use map and filter methods"
            }
        ],
        "specific_selectors": [
            "when user is vegetarian",
            "when user prefers quiet places",
            "when user is traveling in July"
        ]
    }