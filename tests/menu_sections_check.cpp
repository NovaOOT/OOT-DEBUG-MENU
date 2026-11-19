// Exercise section jumps through the real option rows and ImGui navigation, including off-screen and empty sections.
#define main menu_preview_unused_main
#include "../tools/menu_preview.cpp"
#undef main

static ImGuiID row_ids[3];
static float section_y[3], scroll_y;

static void frame(int step = 0) {
    ImGui::NewFrame();
    ImGui::SetNextWindowPos(ImVec2(0, 0));
    ImGui::SetNextWindowSize(ImVec2(900, 480));
    ImGui::Begin("sections", nullptr, ImGuiWindowFlags_NoSavedSettings);
    opt_reserve_bottom(0);
    opt_section_step(step);
    opt_begin("##sections", 1);
    const char *names[] = {"Warp", "View", "Health"};
    for (int group = 0; group < 3; group++) {
        if (group == 1) opt_group_text("Empty section");
        section_y[group] = ImGui::GetCursorPosY();
        opt_group_text(names[group]);
        for (int row = 0; row < 12; row++) {
            char key[64];
            snprintf(key, sizeof key, "##section%d_row%d", group, row);
            opt_text_row(key, "Setting", "Value", nullptr, "Description", false);
            if (!row) row_ids[group] = ImGui::GetItemID();
        }
    }
    scroll_y = ImGui::GetScrollY();
    opt_end();
    ImGui::End();
    ImGui::Render();
}

static void expect(int group, const char *why) {
    if (GImGui->NavId != row_ids[group]) {
        fprintf(stderr, "%s: wrong focused section\n", why);
        exit(1);
    }
}

int main() {
    ImGui::CreateContext();
    ImGuiIO &io = ImGui::GetIO();
    io.IniFilename = nullptr;
    io.DisplaySize = ImVec2(900, 480);
    io.DeltaTime = 1.0f / 60;
    io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;
    io.Fonts->AddFontDefault();
    unsigned char *pixels;
    int w, h;
    io.Fonts->GetTexDataAsRGBA32(&pixels, &w, &h);
    io.Fonts->SetTexID((ImTextureID)1);
    skin_style(ImGui::GetStyle());
    opt_focus_first();
    frame(); frame();
    expect(0, "Initial Warp focus");
    frame(1); frame();
    expect(1, "ZR skips an empty section and reaches View");
    if (scroll_y <= 0 || scroll_y > section_y[1]) return 2;
    frame(1); frame();
    expect(2, "ZR reaches Health");
    frame(1); frame();
    expect(2, "Last section stops at Health");
    frame(-1); frame();
    expect(1, "ZL reaches View");
    frame(-1); frame();
    expect(0, "ZL skips the empty section and reaches Warp");
    frame(-1); frame();
    expect(0, "First section stops at Warp");
    if (scroll_y > section_y[0]) {
        fprintf(stderr, "Warp heading hidden: scroll %.1f, heading %.1f\n", scroll_y, section_y[0]);
        return 3;
    }
    ImGui::DestroyContext();
    puts("Section navigation: Warp -> View -> Health, reverse, boundaries, heading visibility and empty sections passed");
}
