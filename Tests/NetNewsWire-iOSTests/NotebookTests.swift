import Foundation
import Testing
@testable import NetNewsWire

@MainActor @Suite struct NotebookTests {

	private let key = NotebookArticleKey(accountID: "local-fixture", articleID: "article-1")

	@Test func exactSnapshotUsesValidatedPosition() throws {
		let text = "A useful passage for a native notebook."
		let snapshot = try makeSnapshot(text)
		let selection = NSRange(location: 0, length: text.utf16.count)
		let anchor = try NotebookAnchor(snapshot: snapshot, selection: selection)
		#expect(anchor.resolve(in: snapshot) == .exact(selection))
	}

	@Test func wholePassageRelocatesAfterUnrelatedPrepend() throws {
		let text = "A useful passage for a native notebook."
		let anchor = try NotebookAnchor(snapshot: makeSnapshot(text), selection: NSRange(location: 0, length: text.utf16.count))
		let changed = try makeSnapshot("New introduction. " + text)
		#expect(anchor.resolve(in: changed) == .relocated(NSRange(location: 18, length: text.utf16.count)))
	}

	@Test func repeatedWholePassageIsAmbiguous() throws {
		let text = "A useful passage for a native notebook."
		let anchor = try NotebookAnchor(snapshot: makeSnapshot(text), selection: NSRange(location: 0, length: text.utf16.count))
		#expect(anchor.resolve(in: try makeSnapshot(text + "\n" + text)) == .ambiguous)
	}

	@Test func missingPassageDoesNotAttachToAnotherPosition() throws {
		let snapshot = try makeSnapshot("A useful passage for a native notebook.")
		let anchor = try NotebookAnchor(snapshot: snapshot, selection: NSRange(location: 2, length: 6))
		#expect(anchor.resolve(in: try makeSnapshot("The selected passage was deleted.")) == .missing)
	}

	@Test func contextDisambiguatesRepeatedShortQuotes() throws {
		let text = "before target after"
		let anchor = try NotebookAnchor(snapshot: makeSnapshot(text), selection: NSRange(location: 7, length: 6))
		let changed = try makeSnapshot("other target context\n" + text)
		#expect(anchor.resolve(in: changed) == .relocated(NSRange(location: 28, length: 6)))
	}

	@Test func changedContextDoesNotReuseAShortQuote() throws {
		let anchor = try NotebookAnchor(snapshot: makeSnapshot("before target after"), selection: NSRange(location: 7, length: 6))
		#expect(anchor.resolve(in: try makeSnapshot("different target meaning")) == .missing)
	}

	@Test func invalidSurrogateBoundaryAndEmptySelectionAreRejected() throws {
		let snapshot = try makeSnapshot("😀 A useful passage for a native notebook.")
		#expect(throws: (any Error).self) { try NotebookAnchor(snapshot: snapshot, selection: NSRange(location: 1, length: 1)) }
		#expect(throws: (any Error).self) { try NotebookAnchor(snapshot: snapshot, selection: NSRange(location: 0, length: 0)) }
		#expect(NotebookSnapshot.validatedRange(NSRange(location: Int.max - 1, length: 4), in: snapshot.text) == nil)
		#expect(NotebookSnapshot.validatedRange(NSRange(location: 0, length: 1), in: snapshot.text) == nil)
	}

	@Test func unicodeQuoteSurvivesSerializationAndRelocation() throws {
		let text = "Read 👩🏽‍💻 and café with care."
		let anchor = try NotebookAnchor(snapshot: makeSnapshot(text), selection: NSRange(location: 0, length: text.utf16.count))
		let restored = try JSONDecoder().decode(NotebookAnchor.self, from: JSONEncoder().encode(anchor))
		#expect(restored == anchor)
		#expect(restored.resolve(in: try makeSnapshot("Intro. " + text)) == .relocated(NSRange(location: 7, length: text.utf16.count)))
	}

	@Test func acceptedUnicodeSelectionsRemainSerializable() throws {
		let snapshot = try makeSnapshot("😀 👩🏽‍💻 café 🇺🇸")
		for start in 0..<snapshot.text.utf16.count {
			for length in 1...(snapshot.text.utf16.count - start) {
				let selection = NSRange(location: start, length: length)
				guard let anchor = try? NotebookAnchor(snapshot: snapshot, selection: selection) else {
					continue
				}
				#expect(anchor.isValid)
				#expect(anchor.quote.utf16.count == length)
				#expect(try JSONDecoder().decode(NotebookAnchor.self, from: JSONEncoder().encode(anchor)) == anchor)
			}
		}
	}

	@Test func savedNoteAndOriginalQuoteSurviveStoreReopenAndRepair() throws {
		try withStore { store, file in
			let text = "A useful passage for a native notebook."
			let snapshot = try makeSnapshot(text)
			let saved = try store.add(snapshot: snapshot, selection: NSRange(location: 0, length: text.utf16.count), note: "Why it matters")
			#expect(try NotebookStore(fileURL: file).notes(for: key) == [saved])
			let replacement = try makeSnapshot("A replacement passage selected explicitly.")
			try store.repair(id: saved.id, snapshot: replacement, selection: NSRange(location: 0, length: replacement.text.utf16.count))
			let repaired = try #require(NotebookStore(fileURL: file).notes(for: key).first)
			#expect(repaired.originalText == text)
			#expect(repaired.anchors.first?.quote == text)
			#expect(repaired.anchors.count == 2)
			#expect(repaired.note == saved.note)
			#expect(repaired.anchors.last?.resolve(in: replacement) == .exact(NSRange(location: 0, length: replacement.text.utf16.count)))
		}
	}

	@Test func repairCannotMoveANoteToAnotherArticle() throws {
		try withStore { store, _ in
			let snapshot = try makeSnapshot("A useful passage for a native notebook.")
			let saved = try store.add(snapshot: snapshot, selection: NSRange(location: 0, length: 5), note: "Keep identity")
			let other = try NotebookSnapshot(key: NotebookArticleKey(accountID: key.accountID, articleID: "other"), title: "Other", text: snapshot.text)
			#expect(throws: (any Error).self) { try store.repair(id: saved.id, snapshot: other, selection: NSRange(location: 0, length: 5)) }
			#expect(try store.notes(for: key) == [saved])
		}
	}

	@Test func corruptArchiveIsPreservedAndCannotBeOverwritten() throws {
		try withStore { _, file in
			let broken = Data("not valid notebook JSON".utf8)
			try broken.write(to: file)
			let store = NotebookStore(fileURL: file)
			let snapshot = try makeSnapshot("A useful passage for a native notebook.")
			#expect(throws: (any Error).self) { try store.add(snapshot: snapshot, selection: NSRange(location: 0, length: 5), note: "Do not reset") }
			#expect(try Data(contentsOf: file) == broken)
		}
	}

	@Test func failedWriteDoesNotReportASavedNote() throws {
		try withStore { _, file in
			try Data("blocking parent file".utf8).write(to: file)
			let store = NotebookStore(fileURL: file.appendingPathComponent("notes.json"))
			let snapshot = try makeSnapshot("A useful passage for a native notebook.")
			#expect(throws: (any Error).self) { try store.add(snapshot: snapshot, selection: NSRange(location: 0, length: 5), note: "Must fail") }
			#expect(try store.notes(for: key).isEmpty)
			#expect(try Data(contentsOf: file) == Data("blocking parent file".utf8))
		}
	}

	@Test func removalPersistsWithoutRemovingOtherNotes() throws {
		try withStore { store, file in
			let snapshot = try makeSnapshot("A useful passage for a native notebook.")
			let first = try store.add(snapshot: snapshot, selection: NSRange(location: 0, length: 5), note: "First")
			let second = try store.add(snapshot: snapshot, selection: NSRange(location: 2, length: 6), note: "Second")
			try store.remove(id: first.id, key: key)
			#expect(try NotebookStore(fileURL: file).notes(for: key) == [second])
		}
	}

	@Test func editCorpusReportsAttachmentTradeoffs() throws {
		let passage = "A useful passage for a native notebook."
		let unicode = "Read 👩🏽‍💻 and café with care."
		let cases: [AttachmentCase] = [
			AttachmentCase("unchanged", original: passage, updated: passage, target: NSRange(location: 0, length: passage.utf16.count)),
			AttachmentCase("prepend", original: passage, updated: "New introduction. " + passage,
			               target: NSRange(location: 18, length: passage.utf16.count)),
			AttachmentCase("deleted-long-document", original: passage, updated: "No retained passage remains in this article.", target: nil),
			AttachmentCase("indistinguishable-duplicates", original: passage, updated: passage + "\n" + passage, target: nil),
			AttachmentCase("earlier-decoy", original: "before target after", selection: NSRange(location: 7, length: 6),
			               updated: "other target context\nbefore target after", target: NSRange(location: 28, length: 6)),
			AttachmentCase("retained-quote-changed-context", original: "before target after", selection: NSRange(location: 7, length: 6),
			               updated: "different target meaning", target: NSRange(location: 10, length: 6)),
			AttachmentCase("unicode-prepend", original: unicode, updated: "Intro. " + unicode,
			               target: NSRange(location: 7, length: unicode.utf16.count)),
			AttachmentCase("same-length-quote-edit", original: passage, updated: "A useful passage for a native noteb00k.",
			               target: NSRange(location: 0, length: passage.utf16.count)),
			AttachmentCase("deleted-short-document", original: passage, updated: "Removed.", target: nil),
			AttachmentCase("many-indistinguishable-copies", original: "before target after", selection: NSRange(location: 7, length: 6),
			               updated: String(repeating: "before target after ", count: 501), target: nil),
			AttachmentCase("whitespace-in-quote", original: passage, updated: "A useful  passage for a native notebook.",
			               target: NSRange(location: 0, length: passage.utf16.count + 1))
		]
		var totals = ["offset": AttachmentScore(), "firstQuote": AttachmentScore(), "conservative": AttachmentScore()]
		for item in cases {
			let original = try makeSnapshot(item.original)
			let anchor = try NotebookAnchor(snapshot: original, selection: item.selection)
			let changed = try makeSnapshot(item.updated)
			let first = (item.updated as NSString).range(of: anchor.quote, options: .literal)
			let offset = NotebookSnapshot.validatedRange(item.selection, in: item.updated) == nil ? nil : item.selection
			let firstQuote = first.location == NSNotFound ? nil : first
			let conservative: NSRange?
			switch anchor.resolve(in: changed) {
			case .exact(let range), .relocated(let range): conservative = range
			case .ambiguous, .missing: conservative = nil
			}
			for (name, actual) in [("offset", offset), ("firstQuote", firstQuote), ("conservative", conservative)] {
				totals[name, default: AttachmentScore()].record(actual, target: item.target)
			}
			print("FEEDMARK_CASE \(item.name) target=\(String(describing: item.target)) conservative=\(String(describing: conservative))")
		}
		#expect(totals["offset"] == AttachmentScore(correct: 2, incorrectOrUnsupported: 8, unresolved: 1))
		#expect(totals["firstQuote"] == AttachmentScore(correct: 4, incorrectOrUnsupported: 3, unresolved: 4))
		#expect(totals["conservative"] == AttachmentScore(correct: 4, incorrectOrUnsupported: 0, unresolved: 7))
		print("FEEDMARK_CORPUS " + String(decoding: try JSONEncoder().encode(totals), as: UTF8.self))
	}

	private struct AttachmentCase {
		let name: String
		let original: String
		let selection: NSRange
		let updated: String
		let target: NSRange?

		init(_ name: String, original: String, selection: NSRange? = nil, updated: String, target: NSRange?) {
			self.name = name
			self.original = original
			self.selection = selection ?? NSRange(location: 0, length: original.utf16.count)
			self.updated = updated
			self.target = target
		}
	}

	private struct AttachmentScore: Codable, Equatable {
		var correct = 0
		var incorrectOrUnsupported = 0
		var unresolved = 0

		mutating func record(_ actual: NSRange?, target: NSRange?) {
			guard let actual else {
				unresolved += 1
				return
			}
			if actual == target {
				correct += 1
			} else {
				incorrectOrUnsupported += 1
			}
		}
	}

	private func makeSnapshot(_ text: String) throws -> NotebookSnapshot {
		try NotebookSnapshot(key: key, title: "Synthetic article", text: text)
	}

	private func withStore(_ body: (NotebookStore, URL) throws -> Void) throws {
		let directory = FileManager.default.temporaryDirectory.appendingPathComponent("NotebookTests-" + UUID().uuidString)
		try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
		defer { try? FileManager.default.removeItem(at: directory) }
		let file = directory.appendingPathComponent("notes.json")
		try body(NotebookStore(fileURL: file), file)
	}
}
