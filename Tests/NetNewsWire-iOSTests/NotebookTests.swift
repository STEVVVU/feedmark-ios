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
