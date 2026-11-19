// OOT-PC's forest and brass menu chrome. All ornaments are scalable paths, independent of the game's textures.
// The offline preview uses these same paths, categories and footer widgets as the running menu.
#include <math.h>
#include "imgui.h"
#include "imgui_internal.h"
#include "ui/menu_skin.h"

namespace {
ImVec2 pos, extent;
float side, footer;
ImFont *heading;
const ImU32 gold = IM_COL32(211, 176, 94, 255), pale = IM_COL32(246, 223, 157, 255);

ImVec2 add(ImVec2 p, float x, float y) { return ImVec2(p.x + x, p.y + y); }

void jewel(ImDrawList *d, ImVec2 p, float r, bool green = true) {
    d->AddQuadFilled(add(p, 0, -r), add(p, r, 0), add(p, 0, r), add(p, -r, 0), gold);
    d->AddQuadFilled(add(p, 0, -r * .7f), add(p, r * .7f, 0), add(p, 0, r * .7f), add(p, -r * .7f, 0),
                     green ? IM_COL32(38, 91, 51, 255) : IM_COL32(107, 82, 36, 255));
    d->AddTriangleFilled(add(p, 0, -r * .6f), add(p, r * .55f, 0), p, green ? IM_COL32(111, 155, 83, 255) : pale);
}

// A pointed leaf built from two quadratic curves, with a vein and a metallic highlight along its upper edge.
void leaf(ImDrawList *d, ImVec2 p, float angle, float length, float width, ImU32 color) {
    const float x = cosf(angle), y = sinf(angle);
    ImVec2 tip = add(p, x * length, y * length), mid = add(p, x * length * .5f, y * length * .5f);
    d->PathLineTo(p);
    d->PathBezierQuadraticCurveTo(add(mid, -y * width, x * width), tip, 8);
    d->PathBezierQuadraticCurveTo(add(mid, y * width, -x * width), p, 8);
    d->PathFillConvex(color);
    d->AddLine(p, tip, IM_COL32(9, 22, 12, 140), .8f);
    d->AddBezierQuadratic(p, add(mid, -y * width, x * width), tip, IM_COL32(247, 218, 143, (color >> 24) / 2), .7f, 8);
}

void branch(ImDrawList *d, ImVec2 p, float length, float angle, float s, int alpha = 255) {
    const float x = cosf(angle), y = sinf(angle);
    ImVec2 tip = add(p, x * length, y * length);
    d->AddBezierQuadratic(p, add(p, x * length * .5f - y * 8 * s, y * length * .5f + x * 8 * s), tip,
                          IM_COL32(166, 132, 64, alpha), 1.1f * s, 16);
    for (int k = 1; k < 8; k++) {
        float t = k / 8.0f, bend = sinf(t * 3.14159f) * 4 * s;
        ImVec2 at = add(p, x * length * t - y * bend, y * length * t + x * bend);
        const float size = (12 + 6 * sinf(t * 3.14159f)) * s;
        leaf(d, at, angle - .8f, size, size * .24f, IM_COL32(188, 153, 81, alpha));
        leaf(d, at, angle + .9f, size * .85f, size * .23f, IM_COL32(127, 111, 58, alpha));
    }
}

void ocarina(ImDrawList *d, ImVec2 p, float s, int alpha = 255) {
    const ImU32 edge = IM_COL32(218, 183, 103, alpha), fill = IM_COL32(24, 49, 29, alpha);
    // Broad chamber with a separate side mouthpiece; the tail and mouthpiece never share an outline.
    ImVec2 mouth[] = {add(p, -18*s, -10*s), add(p, -28*s, -34*s), add(p, -16*s, -39*s), add(p, -5*s, -14*s)};
    d->AddConvexPolyFilled(mouth, 4, fill);
    d->AddPolyline(mouth, 4, edge, ImDrawFlags_Closed, 1.8f*s);
    d->AddLine(add(p,-25*s,-30*s), add(p,-13*s,-35*s), edge, 1.0f*s);
    d->PathLineTo(add(p, 40*s, -12*s));
    d->PathBezierCubicCurveTo(add(p, 11*s, -30*s), add(p, -34*s, -21*s), add(p, -36*s, 5*s), 18);
    d->PathBezierCubicCurveTo(add(p, -36*s, 31*s), add(p, -3*s, 27*s), add(p, 40*s, -12*s), 18);
    d->PathFillConvex(fill);
    d->PathLineTo(add(p, 40*s, -12*s));
    d->PathBezierCubicCurveTo(add(p, 11*s, -30*s), add(p, -34*s, -21*s), add(p, -36*s, 5*s), 18);
    d->PathBezierCubicCurveTo(add(p, -36*s, 31*s), add(p, -3*s, 27*s), add(p, 40*s, -12*s), 18);
    d->PathStroke(edge, ImDrawFlags_Closed, 2*s);
    d->AddBezierCubic(add(p,-30*s,10*s), add(p,-26*s,27*s), add(p,0,20*s), add(p,32*s,-8*s),
                      IM_COL32(116, 131, 72, alpha), 1*s, 20);
    const float holes[][3] = {{-22,3,4},{-10,-7,3.3f},{3,-10,3},{16,-10,2.5f},{-9,10,3.6f},{5,4,3}};
    for (auto &h : holes) {
        d->AddCircleFilled(add(p,h[0]*s,h[1]*s),h[2]*s,IM_COL32(4,14,8,alpha),12);
        d->AddCircle(add(p,h[0]*s,h[1]*s),h[2]*s,edge,12,.8f*s);
    }
}

void icon(ImDrawList *d, ImVec2 p, int kind, float s, ImU32 c) {
    const float t = 1.6f*s;
    auto line = [&](float x,float y,float a,float b) { d->AddLine(add(p,x*s,y*s),add(p,a*s,b*s),c,t); };
    auto box = [&](float x,float y,float a,float b,float r=0) { d->AddRect(add(p,x*s,y*s),add(p,a*s,b*s),c,r*s,0,t); };
    if (kind == 0) { box(-13,-9,13,8,1); line(0,8,0,14); line(-7,14,7,14); }
    else if (kind == 1) { line(-9,11,10,-12); line(10,-12,13,-13); line(13,-13,12,-8); line(-11,0,0,10); line(-6,6,-12,13); }
    else if (kind == 2) {
        ImVec2 pts[] = {add(p,-12*s,-4*s),add(p,-6*s,-4*s),add(p,1*s,-11*s),add(p,1*s,11*s),add(p,-6*s,4*s),add(p,-12*s,4*s)};
        d->AddPolyline(pts,6,c,ImDrawFlags_Closed,t);
        d->AddBezierQuadratic(add(p,6*s,-7*s),add(p,14*s,0),add(p,6*s,7*s),c,t,12);
        d->AddBezierQuadratic(add(p,10*s,-12*s),add(p,22*s,0),add(p,10*s,12*s),c,t,12);
    } else if (kind == 3) {
        d->AddBezierCubic(add(p,-13*s,8*s),add(p,-18*s,-17*s),add(p,18*s,-17*s),add(p,13*s,8*s),c,t,24);
        line(-13,8,-7,3); line(-7,3,7,3); line(7,3,13,8); line(-9,-4,-3,-4); line(-6,-7,-6,-1);
        d->AddCircleFilled(add(p,7*s,-5*s),1.6f*s,c); d->AddCircleFilled(add(p,10*s,-1*s),1.6f*s,c);
    } else if (kind == 4) {
        box(-11,-10,11,11,2); d->AddCircleFilled(add(p,0,-11*s),4*s,IM_COL32(10,24,15,255));
        d->AddCircle(add(p,0,-13*s),4*s,c,12,t); line(-3,4,3,4); line(0,1,0,7);
    } else if (kind == 5) {
        line(-13,-8,-6,-8); line(-6,-8,6,8); line(6,8,13,8); line(-13,8,-6,8); line(-6,8,6,-8); line(6,-8,13,-8);
        line(9,-12,13,-8); line(9,-4,13,-8); line(9,4,13,8); line(9,12,13,8);
    } else if (kind == 6) {
        d->AddCircle(add(p,-6*s,-6*s),4*s,c,16,t);
        d->AddCircle(add(p,7*s,-6*s),4*s,c,16,t);
        d->AddBezierQuadratic(add(p,-15*s,11*s),add(p,-6*s,-4*s),add(p,3*s,11*s),c,t,16);
        d->AddBezierQuadratic(add(p,-2*s,11*s),add(p,7*s,-4*s),add(p,16*s,11*s),c,t,16);
    } else if (kind == 8) {   // a gauge: the Performance tab
        d->AddBezierCubic(add(p,-13*s,7*s),add(p,-17*s,-15*s),add(p,17*s,-15*s),add(p,13*s,7*s),c,t,24);
        line(-13,7,13,7); line(0,4,7,-7); d->AddCircleFilled(add(p,0,4*s),2.2f*s,c);
        line(-9,-2,-7,-1); line(0,-9,0,-7); line(9,-2,7,-1);
    } else { line(-7,-9,-15,0); line(-15,0,-7,9); line(7,-9,15,0); line(15,0,7,9); line(3,-12,-3,12); }
}

void frame(ImDrawList *d, ImVec2 a, ImVec2 b, float s) {
    for (int i=12;i>0;i--) d->AddRect(add(a,-i*s,-i*s),add(b,i*s,i*s),IM_COL32(0,0,0,6),4*s,0,s);
    d->AddRectFilledMultiColor(a,b,IM_COL32(14,29,20,253),IM_COL32(8,21,15,253),IM_COL32(5,16,11,253),IM_COL32(10,23,15,253));
    // Sparse deterministic fine grain: bounded per panel, never per frame random state.
    unsigned seed=7139;
    for (int i=0;i<2600;i++) {
        seed=1664525u*seed+1013904223u; float x=a.x+(seed&65535)/65535.f*(b.x-a.x);
        seed=1664525u*seed+1013904223u; float y=a.y+(seed&65535)/65535.f*(b.y-a.y);
        d->AddLine(ImVec2(x,y),ImVec2(x+2*s,y),IM_COL32(158,170,110,9),s);
    }
    for (int i=0;i<4;i++) {
        float inset=(2+i*3.f)*s;
        d->AddRect(add(a,inset,inset),add(b,-inset,-inset),i%2 ? IM_COL32(98,79,40,230) : gold,2*s,0,(i==0?1.5f:.7f)*s);
    }
    for (int k=0;k<4;k++) {
        ImVec2 corner(k&1?b.x:a.x,k&2?b.y:a.y);
        float sx=k&1?-1.f:1.f, sy=k&2?-1.f:1.f;
        branch(d,add(corner,sx*18*s,sy*17*s),95*s,sx>0?0:3.14159f,s);
        branch(d,add(corner,sx*17*s,sy*18*s),75*s,sy>0?1.5708f:-1.5708f,s);
        jewel(d,add(corner,sx*17*s,sy*17*s),6*s,false);
    }
    for (int k=0;k<2;k++) {
        ImVec2 p((a.x+b.x)*.5f,k?b.y:a.y);
        d->AddRectFilled(add(p,-95*s,-7*s),add(p,95*s,7*s),IM_COL32(8,21,14,255));
        branch(d,add(p,-17*s,0),65*s,3.14159f,s);
        branch(d,add(p,17*s,0),65*s,0,s);
        jewel(d,p,13*s);
    }
}
}

void skin_option(ImDrawList *d, ImVec2 a, ImVec2 b, float s, bool selected, bool hovered, bool dim) {
    if (selected && skin_widget_art(d,a,b,0,dim?.5f:1.f)) {
        jewel(d,ImVec2(a.x+13*s,(a.y+b.y)*.5f),4*s,false);
        return;
    }
    if (selected || hovered) {
        d->AddRectFilledMultiColor(a,b,IM_COL32(37,65,31,dim?70:210),IM_COL32(25,49,29,dim?70:210),
                                  IM_COL32(15,35,22,dim?70:235),IM_COL32(24,47,25,dim?70:235));
        if (selected) {
            branch(d,ImVec2(b.x-115*s,(a.y+b.y)*.5f),75*s,3.14159f,s,22);
            jewel(d,ImVec2(a.x+12*s,(a.y+b.y)*.5f),4*s,false);
        }
    }
    d->AddLine(ImVec2(a.x,b.y),b,IM_COL32(151,137,91,70),s);
}

int skin_menu_begin(float w,float h,float s,ImFont *title,const char *const *categories,int count,int selected,
                    const char *version,bool navigation,const char *subtitle,const int *icons) {
    heading=title;
    ImDrawList *bg=ImGui::GetBackgroundDrawList();
    bg->AddRectFilled(ImVec2(0,0),ImVec2(w,h),IM_COL32(2,10,6,175));
    const float ww=fminf(w*.92f,1760*s), wh=fminf(h*.88f,950*s);
    ImGui::SetNextWindowPos(ImVec2(w*.5f,h*.5f),ImGuiCond_Always,ImVec2(.5f,.5f));
    ImGui::SetNextWindowSize(ImVec2(ww,wh),ImGuiCond_Always);
    ImGui::PushStyleVar(ImGuiStyleVar_WindowBorderSize,0);
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding,ImVec2(65*s,24*s));
    ImGui::PushStyleColor(ImGuiCol_WindowBg,ImVec4(0,0,0,0));
    ImGui::Begin("##menu",nullptr,ImGuiWindowFlags_NoDecoration|ImGuiWindowFlags_NoMove|ImGuiWindowFlags_NoSavedSettings);
    ImGui::PopStyleColor(); ImGui::PopStyleVar(2);
    pos=ImGui::GetWindowPos(); extent=ImGui::GetWindowSize();
    side=extent.x*.217f; footer=extent.y-125*s;
    ImDrawList *d=ImGui::GetWindowDrawList();
    d->PushClipRectFullScreen();
    const bool painted=skin_forest_art(d,add(pos,-extent.x*.046f,-extent.y*.061f),
                                      add(pos,extent.x*1.042f,extent.y*1.096f));
    if (!painted) frame(d,pos,add(pos,extent.x,extent.y),s);
    d->PopClipRect();
    if (!painted) {
        d->AddRectFilled(add(pos,15*s,15*s),add(pos,side,extent.y-15*s),IM_COL32(2,11,7,100));
        d->AddLine(add(pos,side,16*s),add(pos,side,extent.y-16*s),IM_COL32(165,136,72,180),s);
    }
    const float brand=side/(345*s);
    if (!painted) {
        ocarina(d,add(pos,67*s*brand,83*s),.78f*s*brand);
        branch(d,add(pos,34*s*brand,110*s),60*s*brand,.1f,s*.7f);
    }
    ImFont *f=title?title:ImGui::GetFont();
    d->AddText(f,32*s*brand,add(pos,side*.51f,54*s),SKIN_TEXT,"OOT-PC");
    d->AddText(ImGui::GetFont(),18*s*brand,add(pos,side*.51f,95*s),SKIN_TEXT_DIM,"Ocarina of Time");
    const float start=169*s, rh=fminf(445*s/count,(footer-start-24*s)/count);
    for (int k=0;k<count;k++) {
        ImGui::SetCursorPos(ImVec2(45*s,start+k*rh));
        ImGui::PushID(k);
        if (!navigation) ImGui::BeginDisabled();
        bool pressed=ImGui::InvisibleButton("##category",ImVec2(side-65*s,rh-4*s),ImGuiButtonFlags_EnableNav);
        if (!navigation) ImGui::EndDisabled();
        bool hot=ImGui::IsItemHovered(), focus=ImGui::IsItemFocused()&&GImGui->NavCursorVisible;
        ImVec2 a=ImGui::GetItemRectMin(),b=ImGui::GetItemRectMax();
        if (pressed) selected=k;
        if (selected==k || hot || focus) {
            skin_option(d,a,b,s,selected==k,hot,false);
            d->AddRect(a,b,selected==k?gold:IM_COL32(144,126,78,120),4*s,0,s);
            if (focus) skin_cursor_frame(d,a,b,s,1);
        }
        icon(d,ImVec2(a.x+33*s,(a.y+b.y)*.5f),icons?icons[k]:k,.85f*s,selected==k?pale:gold);
        const float fs=ImGui::GetFontSize();
        d->PushClipRect(add(a,62*s,0),add(b,-5*s,0),true);
        d->AddText(ImVec2(a.x+65*s,(a.y+b.y-fs)*.5f),selected==k?SKIN_TEXT:SKIN_TEXT_DIM,categories[k]);
        d->PopClipRect();
        ImGui::PopID();
    }
    float motif_y=start+count*rh+30*s;
    if (!painted && footer-motif_y>130*s) {
        ImVec2 at=add(pos,side*.5f,(motif_y+footer)*.5f);
        ocarina(d,at,1.35f*s,65);
        branch(d,add(at,-100*s,65*s),125*s,-1.2f,s,55);
        branch(d,add(at,100*s,65*s),125*s,-1.95f,s,55);
    }
    const float main=side+42*s;
    d->AddText(f,52*s,add(pos,main,38*s),SKIN_TEXT,categories[selected]);
    if (*subtitle) d->AddText(add(pos,main,99*s),SKIN_TEXT_DIM,subtitle);
    ImVec2 vs=ImGui::CalcTextSize(version);
    if (extent.x-main>vs.x+290*s)
        d->AddText(add(pos,extent.x-45*s-vs.x,58*s),SKIN_TEXT_DIM,version);
    skin_filigree(d,pos.x+main,pos.x+extent.x-65*s,pos.y+145*s,s,.8f);
    ImGui::SetCursorPos(ImVec2(main,169*s));
    return selected;
}

int skin_menu_finish(float s,const char *resume,const char *restart,const char *quit,const char *status,
                     bool warning,const char *hints) {
    int action=0; ImDrawList *d=ImGui::GetWindowDrawList();
    float x=side+42*s, y=footer;
    d->AddLine(add(pos,x,y),add(pos,extent.x-44*s,y),IM_COL32(169,139,73,190),s);
    d->AddLine(add(pos,40*s,y),add(pos,side-32*s,y),IM_COL32(169,139,73,140),s);
    ImGui::SetCursorPos(ImVec2(42*s,y+24*s));
    if (ImGui::Button(quit,ImVec2(side-80*s,44*s))) action|=SKIN_QUIT;
    float bw=fmaxf(240*s,ImGui::CalcTextSize(resume).x+65*s);
    float rx=extent.x-44*s-bw;
    ImGui::SetCursorPos(ImVec2(rx,y+20*s));
    if (skin_primary_button(resume,ImVec2(bw,55*s),s)) action|=SKIN_RESUME;
    float restart_w=ImGui::CalcTextSize(restart).x+24*s;
    ImGui::SetCursorPos(ImVec2(rx-restart_w-20*s,y+24*s));
    if (ImGui::Button(restart,ImVec2(restart_w,44*s))) action|=SKIN_RESTART;
    float room=rx-restart_w-45*s-x;
    if (room>60*s) {
        d->AddCircleFilled(add(pos,x+6*s,y+45*s),4*s,warning?IM_COL32(240,151,95,255):IM_COL32(112,187,94,255),12);
        d->PushClipRect(add(pos,x+20*s,y+20*s),add(pos,x+room,y+75*s),true);
        d->AddText(add(pos,x+23*s,y+31*s),warning?IM_COL32(239,174,111,255):SKIN_TEXT_DIM,status);
        d->PopClipRect();
    }
    const float fs=ImGui::GetFontSize()*.7f;
    if (room>500*s) {
        ImVec2 size=ImGui::GetFont()->CalcTextSizeA(fs,10000,0,hints);
        d->AddText(ImGui::GetFont(),fs,add(pos,x+room-size.x,y+64*s),SKIN_TEXT_DIM,hints);
    }
    return action;
}
