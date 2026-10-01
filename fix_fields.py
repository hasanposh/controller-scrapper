import json, sys, re
sys.stdout.reconfigure(encoding='utf-8')
data = json.load(open('output/raw_listings.json', 'r', encoding='utf-8'))

ids = ['247637619','254785901','260446243','260516599','260517759','260518081','260518643','260519003','260519631','260520199','260520783','260521093','260521565','260523105','260524507','260526039','260528521','260532721','260535859','260536425','260536585','260536625','260537579']

for lid in ids:
    l = data[lid]
    detail = l.get('_detail_parsed', {})
    body = l.get('_detail_raw', {}).get('body_text', '')

    if not l.get('ManufacturerName') and detail.get('Manufacturer'):
        l['ManufacturerName'] = detail['Manufacturer']

    if not l.get('Year') and detail.get('Year'):
        l['Year'] = detail['Year']

    if not l.get('Model') and detail.get('Model'):
        l['Model'] = detail['Model']

    if not l.get('SerialNumber') and detail.get('Serial Number'):
        l['SerialNumber'] = detail['Serial Number']

    if not l.get('Condition') and detail.get('Condition'):
        l['Condition'] = detail['Condition']

    if not l.get('Description') and detail.get('Description'):
        l['Description'] = detail['Description']

    if not l.get('DealerLocation'):
        loc = l.get('ListingLocation', {})
        if isinstance(loc, dict):
            l['DealerLocation'] = loc.get('FormattedLocation', '')

    if not l.get('Specs'):
        specs = []
        spec_keys = [
            'Total Time', 'Total Landings', 'Registration #',
            'Engine Maintenance Program', 'Engine 1 Time', 'Engine 1 Cycles',
            'Engine 2 Time', 'Engine 2 Cycles', 'Avionics/Radios',
            'Additional Equipment', 'Exterior Notes', 'Interior Notes',
            'Inspection Status', 'Airframe Notes'
        ]
        for k in spec_keys:
            if detail.get(k):
                specs.append({'Key': k, 'Value': detail[k]})
        if specs:
            l['Specs'] = specs

    if not l.get('ListingImageModel'):
        imgs = l.get('_detail_images', [])
        if imgs:
            l['ListingImageModel'] = {'ImageUrl': imgs}

    if l.get('CategoryName'):
        l['CategoryName'] = re.sub(r'\s*FOR SALE\s*$', '', l['CategoryName']).strip()

with open('output/raw_listings.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

for lid in ids[:3]:
    l = data[lid]
    print(f"{lid}:")
    print(f"  ManufacturerName: {l.get('ManufacturerName','')}")
    print(f"  Year: {l.get('Year','')}")
    print(f"  Model: {l.get('Model','')}")
    print(f"  SerialNumber: {l.get('SerialNumber','')}")
    print(f"  Condition: {l.get('Condition','')}")
    print(f"  DealerLocation: {l.get('DealerLocation','')}")
    print(f"  Specs: {len(l.get('Specs',[]))} items")
    print(f"  Images: {len(l.get('ListingImageModel',{}).get('ImageUrl',[]))}")
    print()

print(f"Fixed {len(ids)} listings")
