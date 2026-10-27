// menu.h — meniul de pe TFT-ul consolei, fără nicio dependență de hardware.
//
// Intrări: tastele, encoderul, sliderul și datele venite de la Pi.
// Ieșiri:  comenzi pentru Pi (Cmd) și un „ecran" de 20×16 caractere (Screen),
//          pe care stratul de hardware îl desenează rând cu rând.
// Totul e testat nativ în test/native/logic_test.cpp.
#pragma once
#include <stdint.h>

#ifndef CONSOLE_FW
#define CONSOLE_FW "0.1"
#endif

namespace con {

constexpr int COLS = 20;          // ILI9341 240 px / font 12 px (5×7 scalat ×2)
constexpr int ROWS = 12;          // rândurile de listă de sub titlu
constexpr int MAX_ITEMS = 40;
constexpr int MAX_CMDS = 4;

enum class Key : uint8_t { Select, Back, Action, Talk, BackLong };

struct Item {
    char id[48];
    char text[42];
    uint8_t done;
    int16_t num;
};

struct Cmd {
    enum Kind : uint8_t { None, Get, Do, Focus } kind = None;
    char v[12] = "";   // Get / Focus: rem, plan, targets, scenes, music, lights, home
    char a[16] = "";   // Do: rem_check, plan_done, target_set, scene, music, lights, voice, restart
    char id[48] = "";
    int n = 0;
};

struct Out {
    Cmd cmds[MAX_CMDS];
    int count = 0;
    void add(const Cmd& c) { if (count < MAX_CMDS) cmds[count++] = c; }
};

struct Line {
    char text[COLS + 1];
    bool inv;   // alb pe negru inversat (rândul selectat)
    bool dim;   // gri (bifat, indicii)
    bool operator==(const Line& o) const;
};

struct Screen {
    char header[COLS + 1];   // „CHRONOS        13:35"
    char title[COLS + 1];
    Line rows[ROWS];
    char footer[COLS + 1];   // mesajul de la Pi sau indiciile tastelor
    bool footerErr;
};

enum class View : uint8_t { Home, List, Value, Music, Lights, System, Offline };

class Menu {
public:
    void begin();

    // ── de la Pi ──
    void heartbeat(int h, int m, uint32_t now);
    void setLink(bool chronosAlive);
    void setList(const char* v, const char* title, const Item* items, int n, Out& out);
    void setMusic(bool ok, const char* track, const char* artist, bool playing, int vol, int pos, int dur, uint32_t now);
    void setLights(bool ok, bool on, int bri);
    void message(const char* text, bool err, uint32_t now);

    // ── de la om ──
    void key(Key k, uint32_t now, Out& out);
    void turn(int detents, uint32_t now, Out& out);
    void slider(int pct, uint32_t now, Out& out);
    void tick(uint32_t now, Out& out);   // trimiteri amânate (volum), reîmprospătări

    void render(Screen& s, uint32_t now) const;

    View view() const { return view_; }
    const char* listView() const { return listV_; }
    int selected() const { return sel_; }
    int ledBrightness() const { return ledBri_; }

private:
    enum HomeEntry : uint8_t { HPlan, HRem, HTargets, HMusic, HLights, HSystem, HCOUNT };
    void homeOrder(HomeEntry* order) const;
    void enter(HomeEntry e, Out& out);
    void openList(const char* v, Out& out);
    void focus(Out& out) const;
    void goHome(Out& out);
    void listAction(uint32_t now, Out& out);
    void moveSel(int d, int count, Out& out);

    View view_ = View::Offline;
    bool link_ = false;
    int hour_ = -1, min_ = 0;
    int sel_ = 0, top_ = 0;

    // listele primite de la Pi
    char listV_[12] = "";
    char listTitle_[COLS + 1] = "";
    Item items_[MAX_ITEMS];
    int nItems_ = 0;
    bool listLoaded_ = false;
    int armed_ = -1;            // rândul armat pentru bifare (a doua apăsare confirmă)
    uint32_t armedAt_ = 0;

    // editare valoare (progresul unui target)
    int value_ = 0;

    // muzică
    bool mOk_ = false, mPlay_ = false;
    char mTrack_[42] = "", mArtist_[42] = "";
    int mVol_ = -1, mPos_ = 0, mDur_ = 0;
    uint32_t mAt_ = 0;
    int pendVol_ = -1;
    uint32_t pendVolAt_ = 0;

    // lumini
    bool lOk_ = false, lOn_ = false;
    int lBri_ = 0;
    int pendBri_ = -1;
    uint32_t pendBriAt_ = 0;

    // sistem
    int sysSel_ = 0;
    bool sysEdit_ = false;
    int ledBri_ = 60;
    bool restartArmed_ = false;
    uint32_t restartAt_ = 0;

    int lastSlider_ = -1;
    uint32_t lastGet_ = 0;

    char msg_[COLS + 1] = "";
    bool msgErr_ = false;
    uint32_t msgAt_ = 0;
};

}  // namespace con
