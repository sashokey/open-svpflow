local mp = require "mp"
local normal_graph

if mp.get_property("options/autofit-larger", "") == "" then
    mp.set_property("options/autofit-larger", "100%x100%")
end

local function configure(reset)
    local filters = mp.get_property_native("vf", {})
    local input
    local preceding = false
    for _, filter in ipairs(filters) do
        if filter.label == "svp-input" then
            input = filter
            break
        elseif filter.enabled ~= false then
            preceding = true
        end
    end
    if not input or not input.params or not input.params.graph then return end
    normal_graph = normal_graph or input.params.graph
    local params = not reset and mp.get_property_native("video-dec-params")
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
    if preceding or not width or not height or width <= 0 or height <= 0 or width % 2 ~= 0 or height % 2 ~= 0 then
        local formats = {}
        for format in normal_graph:match("pix_fmts=([^:]+)"):gmatch("[^|]+") do
            if format:match("^yuv444p") then formats[#formats + 1] = format end
        end
        graph = normal_graph:gsub("pix_fmts=[^:]+", "pix_fmts=" .. table.concat(formats, "|"))
    elseif not reset then
        return
    end
    if input.params.graph ~= graph then
        input.params.graph = graph
        mp.set_property_native("vf", filters)
    end
end

mp.add_hook("on_preloaded", 50, function() configure(true) end)
mp.register_event("video-reconfig", function() configure(false) end)

local session_client
local pending_load

mp.register_script_message("open-svpflow-connect", function(client)
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
    end
end)
