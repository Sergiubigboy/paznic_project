#include "menu.h"

#include <stdio.h>
#include <string.h>

namespace con {

namespace {

constexpr uint32_t ARM_MS = 3000;        // a doua apăsare confirmă bifarea
constexpr uint32_t MSG_MS = 3000;        // cât stă mesajul de la Pi în subsol
constexpr uint32_t SEND_IDLE_MS = 250;   // volum/intensitate: trimitem după ce se oprește rotirea
constexpr uint32_t MUSIC_REFRESH_MS = 5000;
constexpr uint32_t LIGHTS_REFRESH_MS = 10000;
constexpr int SLIDER_STEP = 3;           // sub atât e zgomot de la potențiometru

const char* const HOME_LABELS[] = {"Ziua de azi", "Remindere", "Targets", "Muzica", "Lumini", "Sistem"};

int clampi(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }

void copy(char* dst, const char* src, size_t cap) {
    if (!cap) return;
    strncpy(dst, src ? src : "", cap - 1);
    dst[cap - 1] = '\0';
}

void setLine(Line& l, const char* text, bool inv = false, bool dim = false) {
    snprintf(l.text, sizeof l.text, "%-*.*s", COLS, COLS, text ? text : "");
    l.inv = inv;
    l.dim = dim;
}

/** Text stânga + text dreapta pe exact COLS coloane (dreapta are prioritate). */
void split(char* out, const char* left, const char* right) {
    int rl = (int)strlen(right);
    if (rl > COLS) rl = COLS;
    int room = COLS - rl - (rl ? 1 : 0);
    if (room < 0) room = 0;
    snprintf(out, COLS + 1, "%-*.*s%s%s", room, room, left, rl ? " " : "", right + (strlen(right) - rl));
}

void bar(char* out, int width, int pct) {
    int inner = width - 2, k = clampi((pct * inner + 50) / 100, 0, inner);
    out[0] = '[';
    for (int i = 0; i < inner; i++) out[1 + i] = i < k ? '#' : '-';
    out[width - 1] = ']';
    out[width] = '\0';
}

void mmss(char* out, size_t cap, int s) { snprintf(out, cap, "%d:%02d", s / 60, s % 60); }

Cmd get(const char* v) {
    Cmd c;
    c.kind = Cmd::Get;
    copy(c.v, v, sizeof c.v);
    return c;
}

Cmd doCmd(const char* a, const char* id = "", int n = 0) {
    Cmd c;
    c.kind = Cmd::Do;
    copy(c.a, a, sizeof c.a);
    copy(c.id, id, sizeof c.id);
    c.n = n;
    return c;
}

}  // namespace

bool Line::operator==(const Line& o) const {
    return inv == o.inv && dim == o.dim && strcmp(text, o.text) == 0;
}

void Menu::begin() {
    *this = Menu();
}

// ═══════════════════════════════════════════════════════════════
// De la Pi
// ═══════════════════════════════════════════════════════════════

void Menu::heartbeat(int h, int m, uint32_t) {
    hour_ = h;
    min_ = m;
}

void Menu::setLink(bool alive) {
    if (alive == link_) return;
    link_ = alive;
    if (!alive) {
        view_ = View::Offline;
    } else if (view_ == View::Offline) {
        view_ = View::Home;
        sel_ = top_ = 0;
        listV_[0] = '\0';
    }
}

void Menu::setList(const char* v, const char* title, const Item* items, int n, Out& out) {
    if (strcmp(v, listV_) != 0) return;   // a sosit târziu, după ce am ieșit din listă
    nItems_ = clampi(n, 0, MAX_ITEMS);
    memcpy(items_, items, sizeof(Item) * nItems_);
    copy(listTitle_, title, sizeof listTitle_);
    listLoaded_ = true;
    sel_ = clampi(sel_, 0, nItems_ ? nItems_ - 1 : 0);
    if (top_ > sel_) top_ = sel_;
    armed_ = -1;
    if (view_ == View::List) focus(out);
}

void Menu::setMusic(bool ok, const char* track, const char* artist, bool playing, int vol, int pos, int dur,
                    uint32_t now) {
    mOk_ = ok;
    copy(mTrack_, track, sizeof mTrack_);
    copy(mArtist_, artist, sizeof mArtist_);
    mPlay_ = playing;
    if (pendVol_ < 0) mVol_ = vol;
    mPos_ = pos;
    mDur_ = dur;
    mAt_ = now;
}

void Menu::setLights(bool ok, bool on, int bri) {
    lOk_ = ok;
    lOn_ = on;
    if (pendBri_ < 0) lBri_ = bri;
}

void Menu::message(const char* text, bool err, uint32_t now) {
    copy(msg_, text, sizeof msg_);
    msgErr_ = err;
    msgAt_ = now ? now : 1;
}

// ═══════════════════════════════════════════════════════════════
// Navigare
// ═══════════════════════════════════════════════════════════════

void Menu::homeOrder(HomeEntry* o) const {
    // Primul punct din meniu urmează ziua: dimineața planul, seara închiderea ei.
    static const HomeEntry morning[] = {HPlan, HRem, HMusic, HLights, HTargets, HSystem};
    static const HomeEntry day[] = {HRem, HPlan, HMusic, HLights, HTargets, HSystem};
    static const HomeEntry evening[] = {HRem, HTargets, HPlan, HLights, HMusic, HSystem};
    const HomeEntry* src = (hour_ >= 5 && hour_ < 12) || hour_ < 0 ? morning : (hour_ < 19 && hour_ >= 12) ? day : evening;
    memcpy(o, src, sizeof(HomeEntry) * HCOUNT);
}

void Menu::focus(Out& out) const {
    Cmd c;
    c.kind = Cmd::Focus;
    copy(c.v, view_ == View::List || view_ == View::Value ? listV_ : "home", sizeof c.v);
    c.n = sel_;
    out.add(c);
}

void Menu::goHome(Out& out) {
    view_ = View::Home;
    sel_ = top_ = 0;
    listV_[0] = '\0';
    armed_ = -1;
    focus(out);
}

void Menu::openList(const char* v, Out& out) {
    copy(listV_, v, sizeof listV_);
    view_ = View::List;
    sel_ = top_ = 0;
    nItems_ = 0;
    listLoaded_ = false;
    armed_ = -1;
    out.add(get(v));
}

void Menu::enter(HomeEntry e, Out& out) {
    switch (e) {
        case HPlan: openList("plan", out); break;
        case HRem: openList("rem", out); break;
        case HTargets: openList("targets", out); break;
        case HMusic: view_ = View::Music; out.add(get("music")); focus(out); break;
        case HLights: view_ = View::Lights; out.add(get("lights")); focus(out); break;
        case HSystem: view_ = View::System; sysSel_ = 0; sysEdit_ = false; focus(out); break;
        default: break;
    }
}

void Menu::moveSel(int d, int count, Out& out) {
    if (count <= 0) return;
    int prev = sel_;
    sel_ = clampi(sel_ + d, 0, count - 1);
    if (sel_ < top_) top_ = sel_;
    if (sel_ >= top_ + ROWS) top_ = sel_ - ROWS + 1;
    if (sel_ != prev && view_ == View::List) {
        armed_ = -1;
        focus(out);
    }
}

void Menu::listAction(uint32_t now, Out& out) {
    if (!listLoaded_ || nItems_ == 0) return;
    Item& it = items_[sel_];
    const bool rem = strcmp(listV_, "rem") == 0, plan = strcmp(listV_, "plan") == 0;

    if (rem || plan) {
        if (it.done) { message("deja bifat", false, now); return; }
        if (armed_ == sel_ && now - armedAt_ < ARM_MS) {
            armed_ = -1;
            it.done = 1;   // optimist; Pi-ul retrimite lista
            out.add(doCmd(rem ? "rem_check" : "plan_done", it.id));
        } else {
            armed_ = sel_;
            armedAt_ = now;
        }
    } else if (strcmp(listV_, "targets") == 0) {
        view_ = View::Value;
        value_ = clampi(it.num, 0, 100);
    } else if (strcmp(listV_, "scenes") == 0) {
        out.add(doCmd("scene", it.id));
    }
}

void Menu::key(Key k, uint32_t now, Out& out) {
    if (k == Key::Talk) {
        if (!link_) { message("chronos nu ruleaza", true, now); return; }
        out.add(doCmd("voice", "toggle"));
        return;
    }
    if (view_ == View::Offline) return;
    if (k == Key::BackLong) { goHome(out); return; }
    const bool ok = k == Key::Select || k == Key::Action;

    switch (view_) {
        case View::Home: {
            if (!ok) return;
            HomeEntry o[HCOUNT];
            homeOrder(o);
            lastGet_ = now;   // enter() cere deja datele; tick() nu le mai cere o dată
            enter(o[sel_], out);
            break;
        }
        case View::List:
            if (k == Key::Back) {
                if (strcmp(listV_, "scenes") == 0) {   // scenele se deschid din Lumini
                    view_ = View::Lights;
                    listV_[0] = '\0';
                    out.add(get("lights"));
                    focus(out);
                } else {
                    goHome(out);
                }
            } else if (ok) {
                listAction(now, out);
            }
            break;
        case View::Value:
            if (k == Key::Back) {
                view_ = View::List;
            } else if (ok) {
                items_[sel_].num = (int16_t)value_;
                out.add(doCmd("target_set", items_[sel_].id, value_));
                view_ = View::List;
            }
            break;
        case View::Music:
            if (k == Key::Back) goHome(out);
            else if (k == Key::Select) {
                mPlay_ = !mPlay_;
                out.add(doCmd("music", mPlay_ ? "play" : "pause"));
            } else if (k == Key::Action) {
                out.add(doCmd("music", "next"));
            }
            break;
        case View::Lights:
            if (k == Key::Back) goHome(out);
            else if (k == Key::Select) openList("scenes", out);
            else if (k == Key::Action) {
                lOn_ = !lOn_;
                out.add(doCmd("lights", lOn_ ? "on" : "off"));
            }
            break;
        case View::System:
            if (k == Key::Back) {
                if (sysEdit_) sysEdit_ = false;
                else goHome(out);
            } else if (ok && sysSel_ == 0) {
                if (restartArmed_ && now - restartAt_ < ARM_MS) {
                    restartArmed_ = false;
                    out.add(doCmd("restart"));
                } else {
                    restartArmed_ = true;
                    restartAt_ = now;
                }
            } else if (ok && sysSel_ == 1) {
                sysEdit_ = !sysEdit_;
            }
            break;
        default:
            break;
    }
}

void Menu::turn(int d, uint32_t now, Out& out) {
    if (d == 0) return;
    switch (view_) {
        case View::Home: moveSel(d, HCOUNT, out); break;
        case View::List: moveSel(d, nItems_, out); break;
        case View::Value: value_ = clampi(value_ + 5 * d, 0, 100); break;
        case View::Music: {
            int base = pendVol_ >= 0 ? pendVol_ : (mVol_ >= 0 ? mVol_ : 50);
            pendVol_ = clampi(base + 5 * d, 0, 100);
            pendVolAt_ = now;
            break;
        }
        case View::Lights: {
            int base = pendBri_ >= 0 ? pendBri_ : (lOn_ ? lBri_ : 0);
            pendBri_ = clampi(base + 5 * d, 1, 100);
            pendBriAt_ = now;
            break;
        }
        case View::System:
            if (sysEdit_) ledBri_ = clampi(ledBri_ + 5 * d, 5, 100);
            else sysSel_ = clampi(sysSel_ + d, 0, 1);
            break;
        default: break;
    }
}

void Menu::slider(int pct, uint32_t now, Out&) {
    pct = clampi(pct, 0, 100);
    if (lastSlider_ < 0) { lastSlider_ = pct; return; }   // prima citire e poziția de pornire, nu o comandă
    if (pct - lastSlider_ < SLIDER_STEP && lastSlider_ - pct < SLIDER_STEP) return;
    lastSlider_ = pct;
    if (!link_) return;
    // Sliderul e volumul — doar în Lumini e intensitatea.
    if (view_ == View::Lights) {
        pendBri_ = clampi(pct, 1, 100);
        pendBriAt_ = now;
    } else {
        pendVol_ = pct;
        pendVolAt_ = now;
    }
}

void Menu::tick(uint32_t now, Out& out) {
    if (pendVol_ >= 0 && now - pendVolAt_ >= SEND_IDLE_MS) {
        mVol_ = pendVol_;
        out.add(doCmd("music", "set_volume", pendVol_));
        pendVol_ = -1;
    }
    if (pendBri_ >= 0 && now - pendBriAt_ >= SEND_IDLE_MS) {
        lBri_ = pendBri_;
        lOn_ = true;
        out.add(doCmd("lights", "bri", pendBri_));
        pendBri_ = -1;
    }
    if (view_ == View::Music && now - lastGet_ >= MUSIC_REFRESH_MS) {
        lastGet_ = now;
        out.add(get("music"));
    } else if (view_ == View::Lights && now - lastGet_ >= LIGHTS_REFRESH_MS) {
        lastGet_ = now;
        out.add(get("lights"));
    }
}

// ═══════════════════════════════════════════════════════════════
// Desen
// ═══════════════════════════════════════════════════════════════

void Menu::render(Screen& s, uint32_t now) const {
    char buf[64], right[16];
    for (auto& r : s.rows) setLine(r, "");

    if (hour_ >= 0) snprintf(right, sizeof right, "%02d:%02d", hour_, min_);
    else right[0] = '\0';
    split(s.header, "CHRONOS", right);
    s.title[0] = '\0';
    const char* hint = "";

    switch (view_) {
        case View::Offline:
            copy(s.title, "CONSOLA", sizeof s.title);
            setLine(s.rows[1], "astept chronos...");
            setLine(s.rows[3], "pi-ul porneste sau", false, true);
            setLine(s.rows[4], "chronos nu ruleaza", false, true);
            break;

        case View::Home: {
            copy(s.title, "MENIU", sizeof s.title);
            HomeEntry o[HCOUNT];
            homeOrder(o);
            for (int i = 0; i < HCOUNT; i++) {
                snprintf(buf, sizeof buf, "%s %s", i == sel_ ? ">" : " ", HOME_LABELS[o[i]]);
                setLine(s.rows[i], buf, i == sel_);
            }
            hint = "rot=alege apasa=ok";
            break;
        }

        case View::List: {
            if (listLoaded_ && nItems_) snprintf(right, sizeof right, "%d/%d", sel_ + 1, nItems_);
            else right[0] = '\0';
            split(s.title, listTitle_[0] ? listTitle_ : listV_, right);
            if (!listLoaded_) { setLine(s.rows[0], "se incarca...", false, true); break; }
            if (!nItems_) { setLine(s.rows[0], "lista e goala", false, true); break; }
            const bool targets = strcmp(listV_, "targets") == 0, scenes = strcmp(listV_, "scenes") == 0;
            for (int r = 0; r < ROWS && top_ + r < nItems_; r++) {
                const Item& it = items_[top_ + r];
                if (targets) {
                    snprintf(right, sizeof right, "%d%%", it.num);
                    split(buf, it.text, right);
                } else if (scenes) {
                    snprintf(buf, sizeof buf, "%s", it.text);
                } else {
                    snprintf(buf, sizeof buf, "%s %s", it.done ? "[x]" : "[ ]", it.text);
                }
                setLine(s.rows[r], buf, top_ + r == sel_, it.done);
            }
            if (armed_ == sel_ && now - armedAt_ < ARM_MS) hint = "apasa iar = bifat";
            else if (targets) hint = "apasa = progres";
            else if (scenes) hint = "apasa = porneste";
            else hint = "apasa x2 = bifat";
            break;
        }

        case View::Value: {
            copy(s.title, "PROGRES", sizeof s.title);
            setLine(s.rows[0], items_[sel_].text);
            bar(buf, COLS - 5, value_);
            snprintf(buf + strlen(buf), sizeof buf - strlen(buf), " %3d%%", value_);
            setLine(s.rows[2], buf);
            setLine(s.rows[4], "rot = +/- 5%", false, true);
            hint = "apasa=salv K1=anul";
            break;
        }

        case View::Music: {
            copy(s.title, "MUZICA", sizeof s.title);
            if (!mOk_) { setLine(s.rows[0], "spotify neconectat", false, true); break; }
            if (!mTrack_[0]) { setLine(s.rows[0], "nu canta nimic", false, true); }
            else {
                setLine(s.rows[0], mTrack_);
                if ((int)strlen(mTrack_) > COLS) setLine(s.rows[1], mTrack_ + COLS);
                setLine(s.rows[2], mArtist_, false, true);
                int pos = mPos_ + (mPlay_ ? (int)((now - mAt_) / 1000) : 0);
                if (mDur_ && pos > mDur_) pos = mDur_;
                char t1[8], t2[8];
                mmss(t1, sizeof t1, pos);
                mmss(t2, sizeof t2, mDur_);
                setLine(s.rows[4], mPlay_ ? "> canta" : "|| pauza");
                bar(buf, 10, mDur_ ? pos * 100 / mDur_ : 0);
                snprintf(buf + 10, sizeof buf - 10, " %s/%s", t1, t2);
                setLine(s.rows[5], buf);
            }
            int vol = pendVol_ >= 0 ? pendVol_ : mVol_;
            if (vol >= 0) { snprintf(buf, sizeof buf, "volum %d%%", vol); setLine(s.rows[7], buf); }
            hint = "apasa=play K2=>>";
            break;
        }

        case View::Lights: {
            copy(s.title, "LUMINI", sizeof s.title);
            if (!lOk_) setLine(s.rows[0], "wled offline", false, true);
            else {
                int bri = pendBri_ >= 0 ? pendBri_ : lBri_;
                if (lOn_ || pendBri_ >= 0) snprintf(buf, sizeof buf, "aprinse %d%%", bri);
                else snprintf(buf, sizeof buf, "stinse");
                setLine(s.rows[0], buf);
                bar(buf, COLS, lOn_ || pendBri_ >= 0 ? bri : 0);
                setLine(s.rows[1], buf);
            }
            setLine(s.rows[3], "> scene", false, false);
            hint = "K2=on/off apasa=sc";
            break;
        }

        case View::System: {
            copy(s.title, "SISTEM", sizeof s.title);
            const bool armedR = restartArmed_ && now - restartAt_ < ARM_MS;
            setLine(s.rows[0], armedR ? "> apasa iar=restart" : "> reporneste chronos", sysSel_ == 0);
            snprintf(buf, sizeof buf, sysEdit_ ? "< led-uri %3d%% >" : "> led-uri %3d%%", ledBri_);
            setLine(s.rows[1], buf, sysSel_ == 1);
            setLine(s.rows[3], "fw " CONSOLE_FW, false, true);
            setLine(s.rows[4], link_ ? "chronos: conectat" : "chronos: offline", false, true);
            hint = sysEdit_ ? "rot = luminozitate" : "apasa = ok";
            break;
        }
    }

    if (msgAt_ && now - msgAt_ < MSG_MS) {
        copy(s.footer, msg_, sizeof s.footer);
        s.footerErr = msgErr_;
    } else {
        copy(s.footer, hint, sizeof s.footer);
        s.footerErr = false;
    }
}

}  // namespace con
