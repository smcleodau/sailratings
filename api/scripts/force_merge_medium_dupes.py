import csv
import sys
from collections import defaultdict
from irc_data.db.connection import get_engine

# Import the actual merge logic which safely repoints FKs and handles collisions
from merge_boat_dupes_medium import merge_cluster

def main():
    engine = get_engine()
    clusters = defaultdict(list)
    
    with open('/tmp/boat_dupes_medium_review.csv', 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            clusters[row['cluster_id']].append(int(row['boat_id']))
            
    print(f"Force merging {len(clusters)} clusters (bypassing safety gates)...")
    
    success_count = 0
    fail_count = 0
    
    for cluster_id, boat_ids in clusters.items():
        try:
            rep = merge_cluster(engine, cluster_id, boat_ids)
            if rep.note == "already-merged":
                print(f"[skip] {cluster_id}: already merged")
            else:
                print(f"[ok] {cluster_id}: winner={rep.winner_id} losers={len(rep.loser_ids)}")
                success_count += 1
        except Exception as e:
            print(f"[fail] {cluster_id}: {e}", file=sys.stderr)
            fail_count += 1
            
    print(f"\nDone. Successfully merged: {success_count}, Failed: {fail_count}")

if __name__ == '__main__':
    main()
