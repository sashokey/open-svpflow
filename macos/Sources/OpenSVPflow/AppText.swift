import Foundation

func AppText(_ key: String) -> String {
    Bundle.main.localizedString(forKey: key, value: key, table: "Localizable")
}

func AppDiagnostic(_ message: String) -> String {
    let detailed = ["A readable local movie file is required: ", "mpv could not verify Lua playback hooks: "]
    if let prefix = detailed.first(where: { message.hasPrefix($0) }) {
        return String(format: AppText(prefix + "%@"), String(message.dropFirst(prefix.count)))
    }
    return AppText(message)
}
