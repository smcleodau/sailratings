from irc_data.temporal.orchestrator.board_operator import NotionAdapter

def main():
    adapter = NotionAdapter()
    adapter.append_test_evidence(
        issue_id="3cc37ffef46781d88e2cf515856d6db4", 
        test_command="Dashboard + Worker", 
        output="Created /admin/telemetry page with Jaeger iframe. Updated Temporal worker to use setup_telemetry."
    )
    print("Evidence uploaded!")

if __name__ == '__main__':
    main()
