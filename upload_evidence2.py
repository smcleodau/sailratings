from irc_data.temporal.orchestrator.board_operator import NotionAdapter
import subprocess

def main():
    adapter = NotionAdapter()
    output = subprocess.check_output(["python3", "test_export.py"], text=True)
    adapter.append_test_evidence(issue_id="AD-01-10", test_command="python3 test_export.py", output=output)
    print("Evidence uploaded!")

if __name__ == '__main__':
    main()
