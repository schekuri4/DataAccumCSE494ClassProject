import json

def main():
    p='Work/collected_missing_deps_report.jsonl'
    total=0
    with_candidates=0
    no_candidates=0
    with open(p,'r',encoding='utf-8') as f:
        for ln in f:
            obj=json.loads(ln)
            total+=1
            if obj.get('candidates'):
                with_candidates+=1
            else:
                no_candidates+=1
    print('total',total,'with_candidates',with_candidates,'no_candidates',no_candidates)

if __name__ == '__main__':
    main()
