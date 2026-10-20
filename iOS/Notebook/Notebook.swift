import CryptoKit
import Foundation

struct NotebookArticleKey: Codable, Equatable, Sendable {

	let accountID: String
	let articleID: String

	var isValid: Bool {
		!accountID.isEmpty && !articleID.isEmpty && accountID.utf8.count <= 2048 && articleID.utf8.count <= 2048
	}
}

struct NotebookSnapshot: Sendable {

	let key: NotebookArticleKey
	let title: String
	let text: String
	let digest: String

	init(key: NotebookArticleKey, title: String, text: String) throws {
		guard key.isValid, title.count <= 2000, !text.isEmpty, text.utf16.count <= 100_000 else {
			throw NotebookError.invalidInput("The article must contain between 1 and 100,000 text units.")
		}
		self.key = key
		self.title = title
		self.text = text
		self.digest = Self.hash(text)
	}

	static func hash(_ text: String) -> String {
		SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
	}
}

struct NotebookAnchor: Codable, Equatable, Sendable {

	let quote: String
	let prefix: String
	let suffix: String
	let start: Int
	let length: Int
	let snapshotDigest: String

	init(snapshot: NotebookSnapshot, selection: NSRange) throws {
		guard selection.length > 0, selection.length <= 4000,
		      let range = Range(selection, in: snapshot.text) else {
			throw NotebookError.invalidInput("Select a passage of 1 to 4,000 text units first.")
		}
		quote = String(snapshot.text[range])
		prefix = String(snapshot.text[..<range.lowerBound].suffix(40))
		suffix = String(snapshot.text[range.upperBound...].prefix(40))
		start = selection.location
		length = selection.length
		snapshotDigest = snapshot.digest
	}

	var isValid: Bool {
		!quote.isEmpty && quote.utf16.count <= 4000 && length == quote.utf16.count && start >= 0 && start <= 100_000
			&& prefix.count <= 40 && suffix.count <= 40 && snapshotDigest.count == 64
			&& snapshotDigest.allSatisfy { "0123456789abcdef".contains($0) }
	}

	func resolve(in snapshot: NotebookSnapshot) -> NotebookResolution {
		guard isValid else {
			return .missing
		}
		let original = NSRange(location: start, length: length)
		if snapshot.digest == snapshotDigest, let range = Range(original, in: snapshot.text), snapshot.text[range] == quote {
			return .exact(original)
		}
		let text = snapshot.text as NSString
		var cursor = 0
		var inspected = 0
		var supported = [NSRange]()
		while cursor < text.length {
			let found = text.range(of: quote, options: .literal, range: NSRange(location: cursor, length: text.length - cursor))
			guard found.location != NSNotFound, let range = Range(found, in: snapshot.text) else {
				break
			}
			inspected += 1
			if inspected > 500 {
				return .ambiguous
			}
			let before = snapshot.text[..<range.lowerBound]
			let after = snapshot.text[range.upperBound...]
			let contextMatches = (!prefix.isEmpty || !suffix.isEmpty) && before.hasSuffix(prefix) && after.hasPrefix(suffix)
			let wholePassage = prefix.isEmpty && suffix.isEmpty && quote.count >= 24
			if contextMatches || wholePassage {
				supported.append(found)
				if supported.count > 1 {
					return .ambiguous
				}
			}
			cursor = found.location + 1
		}
		guard let match = supported.first else {
			return .missing
		}
		return .relocated(match)
	}
}

enum NotebookResolution: Equatable, Sendable {

	case exact(NSRange)
	case relocated(NSRange)
	case ambiguous
	case missing

	var label: String {
		switch self {
		case .exact: "Attached to this snapshot"
		case .relocated: "Reattached to matching text"
		case .ambiguous: "Ambiguous attachment. Original quote retained."
		case .missing: "Passage missing or context changed. Original quote retained."
		}
	}
}

struct NotebookNote: Codable, Equatable, Identifiable, Sendable {

	let id: UUID
	let key: NotebookArticleKey
	let articleTitle: String
	let originalText: String
	let note: String
	let anchors: [NotebookAnchor]
	let createdAt: Date
	let updatedAt: Date

	var isValid: Bool {
		guard key.isValid, articleTitle.count <= 2000, originalText.utf16.count <= 100_000,
		      !note.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, note.count <= 4000,
		      !anchors.isEmpty, anchors.count <= 21, anchors.allSatisfy(\.isValid),
		      let first = anchors.first, first.snapshotDigest == NotebookSnapshot.hash(originalText),
		      let range = Range(NSRange(location: first.start, length: first.length), in: originalText) else {
			return false
		}
		return originalText[range] == first.quote
	}
}

enum NotebookError: LocalizedError {

	case invalidInput(String)
	case invalidArchive
	case limitReached

	var errorDescription: String? {
		switch self {
		case .invalidInput(let detail): detail
		case .invalidArchive: "The notebook could not be read safely. Its file has been preserved."
		case .limitReached: "This local notebook holds up to 200 notes and 20 repairs per note."
		}
	}
}

@MainActor final class NotebookStore {

	static let shared = NotebookStore()
	private let fileURL: URL?
	private var cachedNotes: [NotebookNote]?
	private struct Archive: Codable {
		let version: Int
		let notes: [NotebookNote]
	}

	init(fileURL: URL? = nil) {
		self.fileURL = fileURL ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)
			.first?.appendingPathComponent("FeedmarkNotebook/notes.json")
	}

	func notes(for key: NotebookArticleKey) throws -> [NotebookNote] {
		try load().filter { $0.key == key }.sorted { $0.createdAt > $1.createdAt }
	}

	@discardableResult
	func add(snapshot: NotebookSnapshot, selection: NSRange, note: String) throws -> NotebookNote {
		var notes = try load()
		guard notes.count < 200 else {
			throw NotebookError.limitReached
		}
		let anchor = try NotebookAnchor(snapshot: snapshot, selection: selection)
		let now = Date()
		let item = NotebookNote(id: UUID(), key: snapshot.key, articleTitle: snapshot.title, originalText: snapshot.text,
		                        note: note.trimmingCharacters(in: .whitespacesAndNewlines), anchors: [anchor], createdAt: now, updatedAt: now)
		guard item.isValid else {
			throw NotebookError.invalidInput("Write a note of 1 to 4,000 characters.")
		}
		notes.append(item)
		try persist(notes)
		return item
	}

	func repair(id: UUID, snapshot: NotebookSnapshot, selection: NSRange) throws {
		var notes = try load()
		guard let index = notes.firstIndex(where: { $0.id == id && $0.key == snapshot.key }) else {
			throw NotebookError.invalidInput("The note does not belong to this article.")
		}
		let old = notes[index]
		guard old.anchors.count < 21 else {
			throw NotebookError.limitReached
		}
		let anchor = try NotebookAnchor(snapshot: snapshot, selection: selection)
		notes[index] = NotebookNote(id: old.id, key: old.key, articleTitle: old.articleTitle, originalText: old.originalText,
		                            note: old.note, anchors: old.anchors + [anchor], createdAt: old.createdAt, updatedAt: Date())
		try persist(notes)
	}

	func remove(id: UUID, key: NotebookArticleKey) throws {
		let notes = try load().filter { !($0.id == id && $0.key == key) }
		try persist(notes)
	}

	private func load() throws -> [NotebookNote] {
		if let cachedNotes {
			return cachedNotes
		}
		guard let fileURL else {
			throw NotebookError.invalidArchive
		}
		guard FileManager.default.fileExists(atPath: fileURL.path) else {
			cachedNotes = []
			return []
		}
		let attributes = try fileURL.resourceValues(forKeys: [.fileSizeKey])
		guard let size = attributes.fileSize, size <= 32 * 1024 * 1024 else {
			throw NotebookError.invalidArchive
		}
		let archive = try JSONDecoder().decode(Archive.self, from: Data(contentsOf: fileURL))
		guard archive.version == 1, archive.notes.count <= 200, archive.notes.allSatisfy(\.isValid),
		      Set(archive.notes.map(\.id)).count == archive.notes.count else {
			throw NotebookError.invalidArchive
		}
		cachedNotes = archive.notes
		return archive.notes
	}

	private func persist(_ notes: [NotebookNote]) throws {
		guard let fileURL else {
			throw NotebookError.invalidArchive
		}
		let encoder = JSONEncoder()
		encoder.outputFormatting = [.sortedKeys]
		let data = try encoder.encode(Archive(version: 1, notes: notes))
		guard data.count <= 32 * 1024 * 1024 else {
			throw NotebookError.limitReached
		}
		try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
		try data.write(to: fileURL, options: .atomic)
		cachedNotes = notes
	}
}
