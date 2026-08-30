import os
import sys
import anthropic

def run_spec_writer():
    epic_file = "/home/irc-data/code/sailratings/docs/epics/EPIC-05-Human-In-The-Loop-Admin.md"
    if not os.path.exists(epic_file):
        print(f"Error: {epic_file} does not exist.")
        sys.exit(1)
        
    with open(epic_file, "r") as f:
        epic_content = f.read()

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    system_prompt = """
    You are the OpenHands 'Spec Writer' Agent, a highly specialized Technical Architect.
    Your ONLY job is to read the provided Epic and output a detailed Markdown 
    specification document. The output MUST contain:
    - 1. System Architecture
    - 2. Data Models / Schemas
    - 3. API Endpoints or State Machine definitions
    - 4. Acceptance Criteria
    
    Do not write code. Do not output anything other than the raw Markdown specification.
    """

    print("Submitting EPIC-05 to the Spec Writer (Claude 3.5 Sonnet)...")
    
    message = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=4000,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": f"Please generate the Technical Specification for this Epic:\n\n{epic_content}"
            }
        ]
    )

    spec_output = message.content[0].text
    
    output_file = "/home/irc-data/code/sailratings/docs/specs/SPEC-05-Human-In-The-Loop-Admin.md"
    with open(output_file, "w") as f:
        f.write(spec_output)
        
    print(f"Spec Writer completed successfully! Saved to {output_file}")

if __name__ == "__main__":
    run_spec_writer()
