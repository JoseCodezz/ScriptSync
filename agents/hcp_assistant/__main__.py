"""Run a consultation from the command line.

    python -m agents.hcp_assistant "Patient with T2D and CKD, what should I start?" \
        --record examples/patient_case.md
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .assistant import HCPAssistant


async def run(prompt: str, record_path: Path | None, show_raw: bool) -> None:
    record = record_path.read_text() if record_path else None
    assistant = HCPAssistant()

    print(f"Consulting {len(assistant.agent_names)} agent(s)...\n")
    result = await assistant.consult(prompt, patient_record_text=record)

    for response in result.responses:
        if response.error:
            print(f"  [FAILED]   {response.ans_name}: {response.error}")
        else:
            trusted = response.verification.trusted if response.verification else False
            status = "VERIFIED" if trusted else "UNVERIFIED"
            suitability = response.answer.suitability if response.answer else "?"
            print(f"  [{status}] {response.ans_name} -> {suitability}")

    if result.intake.omitted_but_needed:
        print("\nMissing data flagged at intake:")
        for item in result.intake.omitted_but_needed:
            print(f"  - {item}")

    if show_raw:
        for response in result.responses:
            if response.answer:
                print(f"\n--- raw: {response.ans_name} ---")
                print(response.answer.model_dump_json(indent=2))

    print("\n" + "=" * 72)
    print(result.brief)
    print("=" * 72)
    print("\nSynthetic demo data. Decision support only - not medical advice.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the HCP assistant a clinical question")
    parser.add_argument("prompt", help="The physician's question, in their own words")
    parser.add_argument("--record", type=Path, default=None, help="Path to a patient record file")
    parser.add_argument("--raw", action="store_true", help="Also print each agent's raw answer")
    args = parser.parse_args()

    asyncio.run(run(args.prompt, args.record, args.raw))


if __name__ == "__main__":
    main()
