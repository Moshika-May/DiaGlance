"""Build model.js: P(x|D), P(x|not D), P(x) per factor from pooled BRFSS 2020+2022. Run from anywhere: python3 -I tools/build.py"""
import csv, json, re, collections
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent   # repo root, so it works from any cwd
A, B = ROOT / 'data/heart_2020_cleaned.csv', ROOT / 'data/heart_2022_with_nans.csv'
yn = lambda v: {'Yes': 'y', 'No': 'n'}.get(v)
def age(v):
    m = re.search(r'\d+', v or ''); n = int(m.group()) if m else None
    return None if n is None else 'a1' if n < 30 else 'a2' if n < 45 else 'a3' if n < 55 else 'a4' if n < 65 else 'a5'
def bmi(v):
    try: x = float(v)
    except ValueError: return None
    return 'b1' if x < 18.5 else 'b2' if x < 25 else 'b3' if x < 30 else 'b4' if x < 35 else 'b5'
def days(v):  # unhealthy days in last 30 -> 5 levels
    try: x = float(v)
    except ValueError: return None
    return 'd1' if x == 0 else 'd2' if x <= 3 else 'd3' if x <= 7 else 'd4' if x <= 14 else 'd5'
def sleep(v):
    try: x = float(v)
    except ValueError: return None
    return 's1' if x < 5 else 's2' if x < 6 else 's3' if x < 7 else 's4' if x <= 8 else 's5'
gh = lambda v: v or None
# key: (Thai question, [(value, Thai label)], f2020(row), f2022(row))
Q = {
 'age':  ('คุณอายุเท่าไร?', [('a1','18–29 ปี'),('a2','30–44 ปี'),('a3','45–54 ปี'),('a4','55–64 ปี'),('a5','65 ปีขึ้นไป')], lambda r: age(r['AgeCategory']), lambda r: age(r['AgeCategory'])),
 'sex':  ('เพศกำเนิด', [('Female','หญิง'),('Male','ชาย')], lambda r: r['Sex'], lambda r: r['Sex']),
 'bmi':  ('ค่าดัชนีมวลกาย (BMI)', [('b1','ต่ำกว่า 18.5 · ผอม'),('b2','18.5–24.9 · สมส่วน'),('b3','25–29.9 · ท้วม'),('b4','30–34.9 · อ้วน'),('b5','35 ขึ้นไป · อ้วนมาก')], lambda r: bmi(r['BMI']), lambda r: bmi(r['BMI'])),
 'sleep':('ปกตินอนวันละกี่ชั่วโมง?', [('s1','น้อยกว่า 5 ชม.'),('s2','5 ชม.'),('s3','6 ชม.'),('s4','7–8 ชม.'),('s5','9 ชม. ขึ้นไป')], lambda r: sleep(r['SleepTime']), lambda r: sleep(r['SleepHours'])),
 'smoke':('เคยสูบบุหรี่ (รวมกันเกิน 100 มวน)?', [('y','เคย'),('n','ไม่เคย')], lambda r: yn(r['Smoking']), lambda r: None if not r['SmokerStatus'] else 'n' if r['SmokerStatus'] == 'Never smoked' else 'y'),
 'alc':  ('ดื่มแอลกอฮอล์หนักเป็นประจำ?', [('y','ใช่'),('n','ไม่')], lambda r: yn(r['AlcoholDrinking']), lambda r: None),  # 2022 = any drinking (different definition): 2020 only),
 'act':  ('ออกกำลังกายในรอบ 30 วันที่ผ่านมา?', [('y','ออก'),('n','ไม่ออก')], lambda r: yn(r['PhysicalActivity']), lambda r: yn(r['PhysicalActivities'])),
 'phys': ('ใน 30 วันที่ผ่านมา มีกี่วันที่ร่างกายไม่สบาย (ป่วย เจ็บ ปวดเมื่อย)?', [('d1','ไม่มีเลย'),('d2','1–3 วัน'),('d3','4–7 วัน'),('d4','8–14 วัน'),('d5','15 วันขึ้นไป')], lambda r: days(r['PhysicalHealth']), lambda r: days(r['PhysicalHealthDays'])),
 'ment': ('ใน 30 วันที่ผ่านมา มีกี่วันที่รู้สึกเครียด ซึมเศร้า หรืออารมณ์ไม่ดี?', [('d1','ไม่มีเลย'),('d2','1–3 วัน'),('d3','4–7 วัน'),('d4','8–14 วัน'),('d5','15 วันขึ้นไป')], lambda r: days(r['MentalHealth']), lambda r: days(r['MentalHealthDays'])),
 'walk': ('เดินหรือขึ้นบันไดลำบาก?', [('y','ใช่'),('n','ไม่')], lambda r: yn(r['DiffWalking']), lambda r: yn(r['DifficultyWalking'])),
 'heart':('เคยเป็นโรคหัวใจ / หัวใจวาย?', [('y','เคย'),('n','ไม่เคย')], lambda r: yn(r['HeartDisease']), lambda r: 'y' if 'Yes' in (r['HadHeartAttack'], r['HadAngina']) else yn(r['HadHeartAttack']) and 'n'),
 'stroke':('เคยเป็นโรคหลอดเลือดสมอง?', [('y','เคย'),('n','ไม่เคย')], lambda r: yn(r['Stroke']), lambda r: yn(r['HadStroke'])),
 'kidney':('เคยเป็นโรคไต?', [('y','เคย'),('n','ไม่เคย')], lambda r: yn(r['KidneyDisease']), lambda r: yn(r['HadKidneyDisease'])),
 'asthma':('เคยเป็นโรคหอบหืด?', [('y','เคย'),('n','ไม่เคย')], lambda r: yn(r['Asthma']), lambda r: yn(r['HadAsthma'])),
 'skin': ('เคยเป็นมะเร็งผิวหนัง?', [('y','เคย'),('n','ไม่เคย')], lambda r: yn(r['SkinCancer']), lambda r: yn(r['HadSkinCancer'])),
}
# Front-end-only extras. ui = friendlier answers that map onto the dataset's yes/no (v); grp = merged into one checklist step; name = short label for results
EXTRA = {
 'age': {'name': 'อายุ'}, 'sex': {'name': 'เพศ'}, 'bmi': {'name': 'BMI'}, 'sleep': {'name': 'การนอน'}, 'smoke': {'name': 'การสูบบุหรี่', 't': 'ประวัติการสูบบุหรี่ของคุณเป็นแบบไหน?',
   'ui': [{'l':'ไม่เคยสูบเลย','v':'n'},{'l':'เคยลองสูบนิดหน่อย (ไม่ถึง 100 มวนตลอดชีวิต)','v':'n'},{'l':'เคยสูบประจำ แต่เลิกแล้ว','v':'y'},{'l':'สูบเป็นบางวัน','v':'y'},{'l':'สูบทุกวัน','v':'y'}]},
 'phys': {'name': 'ร่างกายไม่สบาย (วัน/เดือน)'}, 'ment': {'name': 'เครียด/อารมณ์ไม่ดี (วัน/เดือน)'},
 'act': {'name': 'การออกกำลังกาย', 't': 'ช่วงนี้คุณขยับตัวหรือออกกำลังกายบ่อยแค่ไหน? (นอกเหนือจากงานที่ทำ)',
   'ui': [{'l':'แทบไม่ได้ขยับเลย','v':'n'},{'l':'นานๆ ครั้ง (เดือนละไม่กี่ครั้ง)','v':'y'},{'l':'สัปดาห์ละ 1–2 วัน','v':'y'},{'l':'สัปดาห์ละ 3–4 วัน','v':'y'},{'l':'เกือบทุกวัน','v':'y'}]},
 'alc': {'name': 'แอลกอฮอล์', 't': 'เรื่องเครื่องดื่มแอลกอฮอล์ ปกติเป็นแบบไหน?',
   'ui': [{'l':'ไม่ดื่มเลย','v':'n'},{'l':'นานๆ ครั้ง ตามโอกาส','v':'n'},{'l':'สัปดาห์ละ 1–2 วัน','v':'n'},{'l':'เกือบทุกวัน ครั้งละไม่มาก','v':'n'},{'l':'ดื่มหนักเป็นประจำ (ครั้งละหลายแก้ว)','v':'y'}]},
 'walk': {'name': 'การเดิน/ขึ้นบันได', 't': 'ถ้าต้องเดินไกลๆ หรือขึ้นบันไดหลายชั้น เป็นยังไงบ้าง?',
   'ui': [{'l':'สบายมาก','v':'n'},{'l':'เหนื่อยบ้างแต่ไหว','v':'n'},{'l':'ลำบากพอสมควร ต้องพักบ่อยๆ','v':'y'},{'l':'ลำบากมาก ต้องมีคนช่วยหรือใช้อุปกรณ์','v':'y'}]},
 'heart': {'name': 'โรคหัวใจ', 'grp': 'hist'}, 'stroke': {'name': 'โรคหลอดเลือดสมอง', 'grp': 'hist'}, 'kidney': {'name': 'โรคไต', 'grp': 'hist'},
 'asthma': {'name': 'หอบหืด', 'grp': 'hist'}, 'skin': {'name': 'มะเร็งผิวหนัง', 'grp': 'hist'},
 'sugar': {'name': 'การกินหวาน'},
}
cnt = {k: {o: [0, 0] for o, _ in Q[k][1]} for k in Q}  # [n_not_D, n_D]
tot = [0, 0]
for path, col, idx in [(A, 'Diabetic', 2), (B, 'HadDiabetes', 3)]:
    for r in csv.DictReader(open(path)):
        v = r[col]
        if not v or 'pregnan' in v.lower() or 'during' in v.lower(): continue  # skip unknown / gestational
        d = int(v == 'Yes'); tot[d] += 1
        for k, q in Q.items():
            x = q[idx](r)
            if x in cnt[k]: cnt[k][x][d] += 1
out = {'prevalence': tot[1] / sum(tot), 'q': []}
for k, (txt, opts, *_ ) in Q.items():
    nN, nD = (sum(cnt[k][o][d] for o, _ in opts) for d in (0, 1))
    out['q'].append({'k': k, 't': txt, **EXTRA.get(k, {}), 'o': [{'v': o, 'l': l,
        'pD': (cnt[k][o][1] + 1) / (nD + len(opts)),      # Laplace-smoothed P(x|D)
        'pN': (cnt[k][o][0] + 1) / (nN + len(opts))} for o, l in opts]})  # P(x|not D)
SUG = [('ตอนสั่งเครื่องดื่ม (ชา กาแฟ นม ชานม) ปกติสั่งหวานแค่ไหน?', ['ไม่หวานเลย','หวานน้อย (25%)','หวานครึ่งหนึ่ง (50%)','หวานปกติ (100%)','หวานมาก / เพิ่มน้ำเชื่อม']),
       ('เครื่องดื่มรสหวาน (น้ำอัดลม ชาเย็น น้ำผลไม้กล่อง) ดื่มบ่อยแค่ไหน?', ['แทบไม่ดื่ม','เดือนละไม่กี่ครั้ง','สัปดาห์ละ 1–3 วัน','เกือบทุกวัน','วันละหลายแก้ว']),
       ('ของหวาน ขนม เบเกอรี่ กินบ่อยแค่ไหน?', ['แทบไม่กิน','เดือนละไม่กี่ครั้ง','สัปดาห์ละ 1–3 วัน','เกือบทุกวัน','วันละหลายครั้ง'])]
# ponytail: sugar not in either CSV -> hand-set likelihoods anchored on PDF example (high-sugar bins .60 vs ~.38); replace with real BRFSS SSB data if added
out['q'].append({'k': 'sugar', 'assumed': True, 'name': 'การกินหวาน', 't': 'การกินหวาน (เฉลี่ย 3 ข้อ)',
    'steps': [{'k': f'sugar{n}', 't': t, 'o': [{'v': str(v), 'l': l} for v, l in enumerate(ls)]} for n, (t, ls) in enumerate(SUG)],
    'o': [{'v': str(v), 'l': l, 'pD': pD, 'pN': pN} for v, l, pD, pN in zip(range(5), ['ไม่หวาน','หวานน้อย','ปานกลาง','หวาน','หวานมาก'], [.06,.12,.22,.30,.30], [.12,.20,.30,.22,.16])]})

# English copy: te/ne = question text/short name, o = labels aligned with the DISPLAYED options (ui if present, else o)
EN = {
 'age': ('How old are you?', 'Age', ['18–29', '30–44', '45–54', '55–64', '65 or older']),
 'sex': ('Sex at birth', 'Sex', ['Female', 'Male']),
 'bmi': ('Body mass index (BMI)', 'BMI', ['Under 18.5 · Underweight', '18.5–24.9 · Healthy', '25–29.9 · Overweight', '30–34.9 · Obese', '35 or above · Severely obese']),
 'sleep': ('How many hours do you usually sleep per day?', 'Sleep', ['Under 5 hrs', '5 hrs', '6 hrs', '7–8 hrs', '9 hrs or more']),
 'smoke': ('What is your smoking history?', 'Smoking', ['Never smoked', 'Tried a little (under 100 cigarettes in my life)', 'Used to smoke regularly, but quit', 'Smoke some days', 'Smoke every day']),
 'alc': ('How would you describe your alcohol drinking?', 'Alcohol', ['Never drink', 'Occasionally, on special occasions', '1–2 days a week', 'Almost every day, a little each time', 'Heavy, regular drinking (several drinks at a time)']),
 'act': ('How often do you move or exercise these days? (besides work)', 'Exercise', ['Hardly at all', 'Occasionally (a few times a month)', '1–2 days a week', '3–4 days a week', 'Almost every day']),
 'phys': ('In the past 30 days, how many days did your body feel unwell (sick, hurt, achy)?', 'Unwell days (per month)', ['None', '1–3 days', '4–7 days', '8–14 days', '15 days or more']),
 'ment': ('In the past 30 days, how many days did you feel stressed, down or in a bad mood?', 'Stressed / low-mood days (per month)', ['None', '1–3 days', '4–7 days', '8–14 days', '15 days or more']),
 'walk': ('How is it when you walk far or climb several flights of stairs?', 'Walking / stairs', ['Very easy', 'A bit tiring but fine', 'Quite hard, need frequent rests', 'Very hard, need help or a device']),
 'heart': ('', 'Heart disease', []), 'stroke': ('', 'Stroke', []), 'kidney': ('', 'Kidney disease', []), 'asthma': ('', 'Asthma', []), 'skin': ('', 'Skin cancer', []),
}
EN_SUGAR = ('Sugar intake (average of 3 questions)', 'Sugar intake', ['Not sweet', 'Light', 'Medium', 'Sweet', 'Very sweet'], [
 ('When ordering a drink (tea, coffee, milk, milk tea), how sweet do you usually order it?', ['Not sweet at all', 'Less sweet (25%)', 'Half sweet (50%)', 'Regular sweet (100%)', 'Extra sweet / added syrup']),
 ('How often do you drink sweet drinks (soda, iced tea, boxed juice)?', ['Hardly ever', 'A few times a month', '1–3 days a week', 'Almost every day', 'Several a day']),
 ('How often do you eat desserts, snacks or bakery?', ['Hardly ever', 'A few times a month', '1–3 days a week', 'Almost every day', 'Several times a day'])])
for q in out['q']:
    if q['k'] == 'sugar':
        q['te'], q['ne'], eo, est = EN_SUGAR
        for o, e in zip(q['o'], eo): o['e'] = e
        for st, (t, es) in zip(q['steps'], est):
            st['te'] = t
            assert len(es) == len(st['o'])
            for o, e in zip(st['o'], es): o['e'] = e
        continue
    q['te'], q['ne'], eo = EN[q['k']]
    shown = q.get('ui') or q['o']
    if q.get('grp'): continue
    assert len(eo) == len(shown), q['k']
    for o, e in zip(shown, eo): o['e'] = e
out['n'] = sum(tot)
open(ROOT / 'model.js', 'w').write('const MODEL=' + json.dumps(out, ensure_ascii=False, separators=(',', ':')) + ';')
print(tot, out['prevalence'])
for q in out['q']: print(q['k'], [(o['v'], round(o['pD'], 3), round(o['pN'], 3)) for o in q['o']])
