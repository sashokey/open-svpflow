local mp = require "mp"
local normal_graph
local render_width, render_height
local viewport_key
local playback_ready = false

if mp.get_property("options/autofit-larger", "") == "" then
    mp.set_property("options/autofit-larger", "100%x100%")
end

local function valid_size(width, height)
    return type(width) == "number" and type(height) == "number"
        and width >= 2 and height >= 2 and width < math.huge and height < math.huge
end

local function rendered_size(osd, unscaled)
    if unscaled == "yes" then return end
    if unscaled == "downscale-big" and valid_size(osd.w, osd.h) then return osd.w, osd.h end
    if valid_size(osd.w, osd.h) and osd.ml and osd.mr and osd.mt and osd.mb then
        local width = osd.w - osd.ml - osd.mr
        local height = osd.h - osd.mt - osd.mb
        if valid_size(width, height) then return width, height end
    end
    local target = mp.get_property_native("video-target-params", {})
    if valid_size(target.w, target.h) then return target.w, target.h end
end

local function fitted_axis(axis, other, limit, other_limit, alignment)
    return ("min(%s,max(%d,trunc(min(%d,%d*%s/%s)/%d)*%d))")
        :format(axis, alignment, limit, other_limit, axis, other, alignment, alignment)
end

local function configure(reset)
    local filters = mp.get_property_native("vf", {})
    local input
    local preceding, following = false, false
    for _, filter in ipairs(filters) do
        if filter.label == "svp-input" then
            input = filter
        elseif filter.label ~= "svp-interpolate" and filter.enabled ~= false then
            if input then following = true else preceding = true end
        end
    end
    if not input or input.enabled == false or not input.params or not input.params.graph then return end
    normal_graph = normal_graph or input.params.graph
    local params = not reset and mp.get_property_native("video-dec-params")
    if not reset and not params then return end
    if not params then
        local video = mp.get_property_number("vid")
        for _, track in ipairs(mp.get_property_native("track-list", {})) do
            if track.type == "video" and (not video or track.id == video) then
                params = {w = track["demux-w"], h = track["demux-h"]}
                if not params.w or not params.h or params.w <= 0 or params.h <= 0 or params.w % 2 ~= 0 or params.h % 2 ~= 0 then
                    break
                end
            end
        end
    end
    local width, height = params and params.w, params and params.h
    local graph = normal_graph
    local full_chroma = preceding or not valid_size(width, height) or width % 2 ~= 0 or height % 2 ~= 0
    if full_chroma then
        local formats = {}
        for format in normal_graph:match("pix_fmts=([^:]+)"):gmatch("[^|]+") do
            if format:match("^yuv444p") then formats[#formats + 1] = format end
        end
        graph = normal_graph:gsub("pix_fmts=[^:]+", "pix_fmts=" .. table.concat(formats, "|"))
    end
    local target_width, target_height = render_width, render_height
    if target_width and not following and mp.get_property("options/video-crop", "") == "" and valid_size(width, height) then
        if mp.get_property_number("video-out-params/rotate", 0) % 180 == 90 then
            target_width, target_height = target_height, target_width
        end
        if preceding or width > target_width or height > target_height then
            local alignment = full_chroma and 1 or 2
            local w = fitted_axis("iw", "ih", target_width, target_height, alignment)
            local h = fitted_axis("ih", "iw", target_height, target_width, alignment)
            local scale = ("scale=w='%s':h='%s'"):format(w, h)
            graph = graph:gsub("^scale=w=iw:h=ih", scale)
        end
    end
    if input.params.graph ~= graph then
        if render_width then
            mp.set_property_native("file-local-options/auto-window-resize", false)
        end
        input.params.graph = graph
        mp.set_property_native("vf", filters)
    end
end

local resize_timer = mp.add_timeout(0.25, function()
    local osd = mp.get_property_native("osd-dimensions", {})
    local unscaled = mp.get_property("options/video-unscaled", "no")
    local crop = mp.get_property("options/video-crop", "")
    local key
    if valid_size(osd.w, osd.h) then
        key = table.concat({osd.w, osd.h, unscaled, crop, mp.get_property("fullscreen", "no")}, ":")
        if key == viewport_key then return end
    end
    local width, height = rendered_size(osd, unscaled)
    if not width and unscaled ~= "yes" then return end
    viewport_key = key
    render_width, render_height = width, height
    configure(false)
end, true)

local function schedule_resize()
    if not playback_ready then return end
    resize_timer:kill()
    resize_timer:resume()
end

local function reset_size()
    playback_ready = false
    resize_timer:kill()
    render_width, render_height = nil, nil
    viewport_key = nil
end

mp.add_hook("on_preloaded", 50, function()
    reset_size()
    configure(true)
end)
mp.register_event("video-reconfig", function() configure(false) end)
mp.register_event("playback-restart", function()
    playback_ready = true
    schedule_resize()
end)
mp.register_event("end-file", reset_size)
mp.observe_property("video-target-params/w", "number", schedule_resize)
mp.observe_property("video-target-params/h", "number", schedule_resize)
mp.observe_property("osd-dimensions", "native", schedule_resize)
mp.observe_property("fullscreen", "bool", schedule_resize)
mp.observe_property("options/video-unscaled", "string", schedule_resize)
mp.observe_property("options/video-crop", "string", schedule_resize)

local session_client
local pending_load
local json = require "mp.utils"
local report_directory = os.getenv("OPEN_SVPFLOW_GRAPH_DIR")
local cli_attempt = 0
local cli_started = false
local cli_failed = false

local function read_cli_report()
    local file = io.open(report_directory .. "/graph-" .. cli_attempt .. ".json", "r")
    if not file then return {} end
    local report = json.parse_json(file:read("*a"))
    file:close()
    return report or {}
end

local function fail_cli(message)
    if cli_failed then return end
    cli_failed = true
    io.stderr:write("Open SVPflow: " .. (read_cli_report().message or message) .. "\n")
    mp.commandv("quit", 2)
end

local function cli_filter_active(filters)
    for _, filter in ipairs(filters or {}) do
        if filter.label == "svp-interpolate" and filter.name == "vapoursynth" and filter.enabled ~= false then
            return true
        end
    end
    return false
end

if report_directory then
    mp.enable_messages("error")
    mp.register_event("log-message", function(event)
        if not session_client and event.prefix:find("vapoursynth", 1, true) then
            fail_cli("The interpolation filter could not process this video.")
        end
    end)
    mp.register_event("playback-restart", function()
        if session_client then return end
        if cli_filter_active(mp.get_property_native("vf"))
            and read_cli_report().target_fps and mp.get_property_native("video-out-params") then
            cli_started = true
        else
            fail_cli("The selected source did not produce an interpolated video frame.")
        end
    end)
    mp.observe_property("vf", "native", function(_, filters)
        if not session_client and cli_started and not cli_filter_active(filters) then
            fail_cli("The interpolation filter failed or was disabled")
        end
    end)
    mp.register_event("end-file", function(event)
        if not session_client and (event.reason == "error" or event.reason == "eof" and not cli_started) then
            fail_cli("The selected source did not produce an interpolated video frame.")
        end
        cli_started = false
    end)
end

local function sample_playback()
    if not session_client or pending_load or mp.get_property_bool("idle-active", true) then return end
    local framedrop = mp.get_property("options/framedrop", "no")
    local sample = {
        title = mp.get_property("media-title"),
        filename = mp.get_property("filename"),
        paused = mp.get_property_bool("pause", false),
        seeking = mp.get_property_bool("seeking", false) or mp.get_property_bool("paused-for-cache", false),
        source_fps = mp.get_property_number("container-fps"),
        filtered_fps = mp.get_property_number("estimated-vf-fps"),
        refresh = mp.get_property_number("display-fps"),
        speed = mp.get_property_number("speed"),
        avsync = mp.get_property_number("avsync"),
    }
    if framedrop:find("vo", 1, true) then sample.output_drops = mp.get_property_number("frame-drop-count") end
    if framedrop:find("decoder", 1, true) then sample.decoder_drops = mp.get_property_number("decoder-frame-drop-count") end
    if mp.get_property_bool("display-sync-active", false) then
        sample.display_delays = mp.get_property_number("vo-delayed-frame-count")
    end
    local payload = json.format_json(sample)
    if payload then mp.commandv("script-message-to", session_client, "open-svpflow-playback-sample", payload) end
end

mp.register_script_message("open-svpflow-connect", function(client)
    if not session_client then mp.add_periodic_timer(2, sample_playback) end
    session_client = client
    mp.commandv("script-message-to", session_client, "open-svpflow-bridge-ready")
end)

mp.register_script_message("open-svpflow-continue", function()
    if pending_load then
        local hook = pending_load
        pending_load = nil
        hook:cont()
    end
end)

mp.add_hook("on_load", 50, function(hook)
    if session_client then
        hook:defer()
        pending_load = hook
        mp.commandv("script-message-to", session_client, "open-svpflow-before-load")
    elseif report_directory then
        cli_attempt = cli_attempt + 1
        cli_started = false
        local file = io.open(report_directory .. "/cli-attempt", "w")
        if not file then
            fail_cli("The interpolation filter could not process this video.")
            return
        end
        file:write(tostring(cli_attempt))
        file:close()
    end
end)
