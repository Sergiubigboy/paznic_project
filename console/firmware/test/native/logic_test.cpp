// Teste native pentru logica consolei — rulează pe PC, fără Pico:
//
//     g++ -std=c++17 -Wall -Ilib/logic test/native/logic_test.cpp lib/logic/*.cpp -o logic_test && ./logic_test
//
// (din console/firmware/)
#include <stdio.h>
#include <string.h>

#include "ledfx.h"
#include "menu.h"
#include "power.h"

using namespace con;

static int pass = 0, fail = 0;
#define CHECK(name, cond)                                              \
    do {                                                               \
        if (cond) { pass++; printf("  PASS  %s\n", name); }            \
        else { fail++; printf("  FAIL  %s  (%s:%d)\n", name, __FILE__, __LINE__); } \
    } while (0)

static Item mk(const char* id, const char* text, int done = 0, int num = 0) {
    Item it{};
    strncpy(it.id, id, sizeof it.id - 1);
    strncpy(it.text, text, sizeof it.text - 1);
    it.done = (uint8_t)done;
    it.num = (int16_t)num;
    return it;
}

static bool has(const Out& o, Cmd::Kind k, const char* a_or_v, const char* id = nullptr, int n = -9999) {
    for (int i = 0; i < o.count; i++) {
        const Cmd& c = o.cmds[i];
        if (c.kind != k) continue;
        const char* key = k == Cmd::Do ? c.a : c.v;
        if (strcmp(key, a_or_v) != 0) continue;
        if (id && strcmp(c.id, id) != 0) continue;
        if (n != -9999 && c.n != n) continue;
        return true;
    }
    return false;
}

static void menuTests() {
    printf("\n[meniu]\n");
    Menu m;
    m.begin();
    Screen s;
    m.render(s, 0);
    CHECK("fara Chronos: ecran de asteptare", m.view() == View::Offline && strstr(s.rows[1].text, "astept"));

    Out o;
    m.key(Key::Talk, 0, o);
    CHECK("fara Chronos: K3 nu trimite nimic", o.count == 0);

    m.setLink(true);
    m.heartbeat(8, 30, 0);
    m.render(s, 0);
    CHECK("dimineata primul punct e ziua de azi", strstr(s.rows[0].text, "Ziua de azi") && s.rows[0].inv);
    CHECK("ceasul apare in antet", strstr(s.header, "08:30") && strncmp(s.header, "CHRONOS", 7) == 0);
    CHECK("antetul are exact 20 de coloane", strlen(s.header) == COLS);

    m.heartbeat(21, 0, 0);
    m.render(s, 0);
    CHECK("seara primul punct sunt reminderele", strstr(s.rows[0].text, "Remindere"));

    // intrăm în remindere
    o = Out();
    m.key(Key::Select, 1000, o);
    CHECK("intrarea in remindere cere lista", has(o, Cmd::Get, "rem"));
    m.render(s, 1000);
    CHECK("pana vine lista: se incarca", strstr(s.rows[0].text, "se incarca"));

    Item items[3] = {mk("r1", "Plateste factura"), mk("r2", "Filament PLA"), mk("r3", "Uda plantele")};
    o = Out();
    m.setList("rem", "REMINDERE", items, 3, o);
    CHECK("lista sosita → focus pentru ecranul mare", has(o, Cmd::Focus, "rem"));
    m.render(s, 1000);
    CHECK("titlul are pozitia 1/3", strstr(s.title, "REMINDERE") && strstr(s.title, "1/3"));
    CHECK("randul selectat e inversat", s.rows[0].inv && !s.rows[1].inv);
    CHECK("bifa goala", strncmp(s.rows[0].text, "[ ] Plateste", 12) == 0);

    o = Out();
    m.turn(1, 1100, o);
    CHECK("encoderul muta selectia si anunta ecranul mare", m.selected() == 1 && has(o, Cmd::Focus, "rem"));
    o = Out();
    m.turn(10, 1100, o);
    CHECK("selectia nu iese din lista", m.selected() == 2);

    o = Out();
    m.key(Key::Action, 4000, o);
    CHECK("prima apasare doar armeaza", o.count == 0);
    m.render(s, 4000);
    CHECK("subsolul cere a doua apasare", strstr(s.footer, "apasa iar"));
    m.key(Key::Action, 4500, o);
    CHECK("a doua apasare bifeaza exact reminderul ales", has(o, Cmd::Do, "rem_check", "r3"));

    o = Out();
    m.key(Key::Action, 10000, o);
    CHECK("bifat deja → nu mai trimite", o.count == 0);

    o = Out();
    m.turn(-1, 11000, o);
    m.key(Key::Action, 11000, o);
    o = Out();
    m.key(Key::Action, 15000, o);
    CHECK("a doua apasare dupa 3s doar rearmeaza", o.count == 0);

    // derulare pe liste lungi
    Item many[30];
    for (int i = 0; i < 30; i++) {
        char t[16];
        snprintf(t, sizeof t, "item %d", i);
        many[i] = mk(t, t);
    }
    o = Out();
    m.setList("rem", "REMINDERE", many, 30, o);
    m.turn(20, 16000, o);
    m.render(s, 16000);
    int invRow = -1;
    for (int r = 0; r < ROWS; r++) if (s.rows[r].inv) invRow = r;
    CHECK("lista lunga deruleaza ca selectatul sa ramana vizibil", m.selected() == 21 && invRow == ROWS - 1 &&
          strstr(s.rows[ROWS - 1].text, "item 21"));

    o = Out();
    m.key(Key::Back, 17000, o);
    CHECK("K1 duce in meniu si curata ecranul mare", m.view() == View::Home && has(o, Cmd::Focus, "home"));

    // targets → editare progres
    m.heartbeat(21, 0, 0);
    o = Out();
    m.turn(1, 18000, o);   // seara: Remindere, Targets, ...
    m.key(Key::Select, 18000, o);
    CHECK("targets cere lista", has(o, Cmd::Get, "targets"));
    Item tg[1] = {mk("Slabit 5kg", "Slabit 5kg", 0, 40)};
    m.setList("targets", "TARGETS", tg, 1, o);
    m.render(s, 18000);
    CHECK("targetul arata procentul la dreapta", strstr(s.rows[0].text, "40%"));
    m.key(Key::Select, 18100, o);
    CHECK("apasare pe target → editare", m.view() == View::Value);
    m.turn(3, 18200, o);
    o = Out();
    m.key(Key::Select, 18300, o);
    CHECK("encoderul schimba cu 5% si salveaza", has(o, Cmd::Do, "target_set", "Slabit 5kg", 55));

    // muzică: volumul pleacă o singură dată, după ce te oprești din rotit
    o = Out();
    m.key(Key::BackLong, 19000, o);
    m.heartbeat(14, 0, 0);   // ziua: Remindere, Ziua, Muzica...
    m.turn(2, 19000, o);
    o = Out();
    m.key(Key::Select, 19000, o);
    CHECK("muzica cere starea", m.view() == View::Music && has(o, Cmd::Get, "music"));
    m.setMusic(true, "Midnight City", "M83", true, 40, 60, 243, 19000);
    o = Out();
    for (int i = 0; i < 4; i++) m.turn(1, 19000 + i * 50, o);
    CHECK("rotind nu trimite inca", o.count == 0);
    m.tick(19400, o);
    CHECK("volumul pleaca o data, la final", o.count == 1 && has(o, Cmd::Do, "music", "set_volume", 60));
    m.render(s, 29000);
    CHECK("progresul piesei curge si fara Pi", strstr(s.rows[5].text, "1:10/4:03"));
    o = Out();
    m.key(Key::Select, 29000, o);
    CHECK("apasare = pauza", has(o, Cmd::Do, "music", "pause"));

    // slider
    Menu m2;
    m2.begin();
    m2.setLink(true);
    o = Out();
    m2.slider(50, 0, o);
    m2.tick(1000, o);
    CHECK("prima citire a sliderului nu e o comanda", o.count == 0);
    m2.slider(51, 1000, o);
    m2.tick(2000, o);
    CHECK("zgomotul de 1% e ignorat", o.count == 0);
    m2.slider(80, 2000, o);
    m2.tick(2300, o);
    CHECK("sliderul e volumul", has(o, Cmd::Do, "music", "set_volume", 80));

    o = Out();
    m2.key(Key::Talk, 3000, o);
    CHECK("K3 porneste/opreste vocea oriunde", has(o, Cmd::Do, "voice", "toggle"));

    m2.setLink(false);
    CHECK("Chronos cade → ecran de asteptare", m2.view() == View::Offline);
    m2.message("bifat: factura", false, 5000);
    m2.render(s, 5000);
    CHECK("mesajul de la Pi apare in subsol", strstr(s.footer, "bifat"));
    m2.render(s, 9000);
    CHECK("si dispare dupa 3s", !strstr(s.footer, "bifat"));

    // sistem: restart cu confirmare
    m2.setLink(true);
    m2.heartbeat(14, 0, 0);
    o = Out();
    m2.turn(5, 0, o);
    m2.key(Key::Select, 100, o);
    CHECK("sistem", m2.view() == View::System);
    o = Out();
    m2.key(Key::Select, 200, o);
    m2.key(Key::Select, 400, o);
    CHECK("restart doar dupa a doua apasare", o.count == 1 && has(o, Cmd::Do, "restart"));

    // fiecare rând are fix 20 de coloane, în orice ecran
    bool ok = true;
    for (int r = 0; r < ROWS; r++) ok = ok && strlen(s.rows[r].text) == COLS;
    CHECK("toate randurile au 20 de coloane", ok);
}

static void powerTests() {
    printf("\n[butonul de pornire]\n");
    Power p;
    PowerAct a = p.update({true, false, false, 1000});
    CHECK("la pornirea Pico-ului asteapta (poate Pi-ul porneste singur)", a == PowerAct::None);
    a = p.update({true, false, false, 21000});
    CHECK("ON + Pi oprit → puls J2", a == PowerAct::PulseJ2 && p.state() == Power::Booting);
    a = p.update({true, false, false, 50000});
    CHECK("asteapta boot-ul", a == PowerAct::None);
    a = p.update({true, true, false, 52000});
    CHECK("VBUS apare → pornit", p.state() == Power::On);

    a = p.update({false, true, false, 60000});
    CHECK("OFF cu Chronos mort: inca asteapta", a == PowerAct::None);
    a = p.update({false, true, true, 65000});
    CHECK("OFF cu Chronos viu → oprire curata", a == PowerAct::SendPowerOff && p.state() == Power::ShuttingDown);
    a = p.update({false, true, false, 100000});
    CHECK("cat se opreste nu mai face nimic", a == PowerAct::None);
    a = p.update({false, false, false, 110000});
    CHECK("VBUS dispare → oprit", p.state() == Power::Off);
    a = p.update({false, false, false, 200000});
    CHECK("OFF ramane oprit", a == PowerAct::None);

    Power q;
    q.update({true, true, true, 0});
    q.update({false, true, false, 1000});
    a = q.update({false, true, false, 31500});
    CHECK("OFF si Chronos mort 30s → dublu puls J2", a == PowerAct::DoublePulseJ2);

    Power r;
    r.update({true, true, true, 0});
    r.update({true, false, false, 1000});   // reboot: VBUS cade o clipa
    a = r.update({true, false, false, 5000});
    CHECK("reboot: nu apasa J2 in perioada de gratie", a == PowerAct::None);
    a = r.update({true, true, false, 15000});
    CHECK("reboot: Pi-ul revine singur", r.state() == Power::On);

    Power f;
    f.update({true, false, false, 0});
    int pulses = 0;
    for (uint32_t t = 20000; t < 400000; t += 1000)
        if (f.update({true, false, false, t}) == PowerAct::PulseJ2) pulses++;
    CHECK("Pi-ul nu porneste → 3 incercari, apoi se opreste din incercat", pulses == 3 && f.state() == Power::Fault);
    f.update({false, false, false, 400000});
    a = f.update({true, false, false, 401000});
    CHECK("OFF→ON dupa eroare reincearca", a == PowerAct::PulseJ2);
}

static void ledTests() {
    printf("\n[LED-uri]\n");
    RGBW mood = parseHex("8b7aff");
    CHECK("culoarea hex", mood.r == 0x8b && mood.g == 0x7a && mood.b == 0xff);
    RGBW bad = parseHex("zz");
    CHECK("hex invalid → violetul implicit", bad.r == 139);
    RGBW off = ledColor(Mood::PiOff, 0, 0, mood, 100);
    CHECK("Pi oprit → stins", !off.r && !off.g && !off.b && !off.w);
    RGBW lo = ledColor(Mood::Listening, 0, 0.0f, mood, 100), hi = ledColor(Mood::Listening, 0, 1.0f, mood, 100);
    CHECK("cand vorbesti, mai tare = mai luminos", hi.b > lo.b);
    RGBW half = ledColor(Mood::Speaking, 0, 1.0f, mood, 50);
    CHECK("luminozitatea din meniu se aplica", half.w > 100 && half.w < 140);

    RGBW strip[36];
    for (auto& p : strip) p = {255, 255, 255, 255};
    float k = limitCurrent(strip, 36, 1200);
    long sum = 0;
    for (auto& p : strip) sum += p.r + p.g + p.b + p.w;
    CHECK("alb complet e limitat la ~1.2A", k < 0.5f && sum * 20 / 255 <= 1220);
    RGBW dim[2] = {{0, 0, 0, 40}, {0, 0, 0, 40}};
    CHECK("sub limita nu se atinge nimic", limitCurrent(dim, 2, 1200) == 1.0f && dim[0].w == 40);

    Wave w;
    for (int i = 0; i < Wave::N; i++) w.push(i / (float)Wave::N);
    CHECK("waveform: ultimul e cel mai nou", w.at(Wave::N - 1) > w.at(0));
    w.push(2.0f);
    CHECK("waveform: nivelul e plafonat la 1", w.at(Wave::N - 1) == 1.0f);
}

int main() {
    menuTests();
    powerTests();
    ledTests();
    printf("\n%d PASS  %d FAIL\n", pass, fail);
    return fail ? 1 : 0;
}
