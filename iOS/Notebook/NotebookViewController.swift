import UIKit

@MainActor final class NotebookViewController: UIViewController {

	private let snapshot: NotebookSnapshot
	private let store: NotebookStore
	private let bodyText = UITextView()
	private let noteField = UITextField()
	private let selectedQuote = UILabel()
	private let feedback = UILabel()
	private let notesStack = UIStackView()
	private var selection: NSRange?

	init(snapshot: NotebookSnapshot, store: NotebookStore = .shared) {
		self.snapshot = snapshot
		self.store = store
		super.init(nibName: nil, bundle: nil)
	}

	required init?(coder: NSCoder) {
		return nil
	}

	override func viewDidLoad() {
		super.viewDidLoad()
		title = "Reading notebook"
		view.backgroundColor = .systemBackground
		navigationItem.rightBarButtonItem = UIBarButtonItem(barButtonSystemItem: .done, target: self, action: #selector(close))
		let scroll = UIScrollView()
		scroll.translatesAutoresizingMaskIntoConstraints = false
		scroll.keyboardDismissMode = .interactive
		view.addSubview(scroll)
		let stack = UIStackView()
		stack.axis = .vertical
		stack.spacing = 16
		stack.translatesAutoresizingMaskIntoConstraints = false
		scroll.addSubview(stack)
		NSLayoutConstraint.activate([
			scroll.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
			scroll.bottomAnchor.constraint(equalTo: view.keyboardLayoutGuide.topAnchor),
			scroll.leadingAnchor.constraint(equalTo: view.leadingAnchor),
			scroll.trailingAnchor.constraint(equalTo: view.trailingAnchor),
			stack.topAnchor.constraint(equalTo: scroll.contentLayoutGuide.topAnchor, constant: 20),
			stack.bottomAnchor.constraint(equalTo: scroll.contentLayoutGuide.bottomAnchor, constant: -24),
			stack.leadingAnchor.constraint(equalTo: scroll.contentLayoutGuide.leadingAnchor, constant: 20),
			stack.trailingAnchor.constraint(equalTo: scroll.contentLayoutGuide.trailingAnchor, constant: -20),
			stack.widthAnchor.constraint(equalTo: scroll.frameLayoutGuide.widthAnchor, constant: -40)
		])
		stack.addArrangedSubview(label(snapshot.title, style: .title2))
		stack.addArrangedSubview(label("Select a passage below, then capture it. This is the article text when you opened the notebook.", style: .subheadline))
		bodyText.text = snapshot.text
		bodyText.font = .preferredFont(forTextStyle: .body)
		bodyText.adjustsFontForContentSizeCategory = true
		bodyText.isEditable = false
		bodyText.isSelectable = true
		bodyText.backgroundColor = .secondarySystemBackground
		bodyText.layer.cornerRadius = 12
		bodyText.textContainerInset = UIEdgeInsets(top: 12, left: 8, bottom: 12, right: 8)
		bodyText.accessibilityIdentifier = "notebook.articleText"
		bodyText.heightAnchor.constraint(equalToConstant: 240).isActive = true
		stack.addArrangedSubview(bodyText)
		stack.addArrangedSubview(button("Capture selected passage", identifier: "notebook.capture") { [weak self] in self?.capture() })
		stack.addArrangedSubview(button("Capture whole article", identifier: "notebook.captureWhole") { [weak self] in
			guard let self else {
				return
			}
			bodyText.selectedRange = NSRange(location: 0, length: snapshot.text.utf16.count)
			capture()
		})
		selectedQuote.numberOfLines = 0
		selectedQuote.font = .preferredFont(forTextStyle: .callout)
		selectedQuote.adjustsFontForContentSizeCategory = true
		selectedQuote.text = "No passage selected"
		stack.addArrangedSubview(selectedQuote)
		noteField.placeholder = "Why this passage matters"
		noteField.accessibilityIdentifier = "notebook.note"
		noteField.borderStyle = .roundedRect
		noteField.font = .preferredFont(forTextStyle: .body)
		noteField.adjustsFontForContentSizeCategory = true
		noteField.returnKeyType = .done
		noteField.addAction(UIAction { [weak self] _ in self?.noteField.resignFirstResponder() }, for: .editingDidEndOnExit)
		stack.addArrangedSubview(noteField)
		stack.addArrangedSubview(button("Save note", identifier: "notebook.save") { [weak self] in self?.save() })
		feedback.numberOfLines = 0
		feedback.font = .preferredFont(forTextStyle: .callout)
		feedback.adjustsFontForContentSizeCategory = true
		feedback.accessibilityIdentifier = "notebook.feedback"
		feedback.text = "Notes are stored in this app's local data. Reopen after refreshing an article to check its attachments."
		stack.addArrangedSubview(feedback)
		stack.addArrangedSubview(label("Saved notes", style: .headline))
		notesStack.axis = .vertical
		notesStack.spacing = 24
		stack.addArrangedSubview(notesStack)
		refreshNotes()
	}

	@objc private func close() {
		dismiss(animated: true)
	}

	private func capture() {
		do {
			let anchor = try NotebookAnchor(snapshot: snapshot, selection: bodyText.selectedRange)
			selection = bodyText.selectedRange
			selectedQuote.text = "Selected quote: " + anchor.quote
			feedback.text = "Passage captured. Add your note and save."
		} catch {
			selection = nil
			selectedQuote.text = "No passage selected"
			show(error)
		}
	}

	private func save() {
		do {
			guard let selection else {
				throw NotebookError.invalidInput("Capture a selected passage first.")
			}
			try store.add(snapshot: snapshot, selection: selection, note: noteField.text ?? "")
			noteField.resignFirstResponder()
			noteField.text = nil
			self.selection = nil
			selectedQuote.text = "No passage selected"
			feedback.text = "Note saved on this device."
			refreshNotes()
		} catch {
			show(error)
		}
	}

	private func refreshNotes() {
		for child in notesStack.arrangedSubviews {
			notesStack.removeArrangedSubview(child)
			child.removeFromSuperview()
		}
		do {
			let notes = try store.notes(for: snapshot.key)
			if notes.isEmpty {
				notesStack.addArrangedSubview(label("No notes for this article yet.", style: .body))
			}
			for note in notes {
				guard let original = note.anchors.first, let latest = note.anchors.last else {
					throw NotebookError.invalidArchive
				}
				let card = UIStackView()
				card.axis = .vertical
				card.spacing = 8
				card.addArrangedSubview(label(note.note, style: .headline))
				card.addArrangedSubview(label("Original quote: " + original.quote, style: .body))
				card.addArrangedSubview(label(latest.resolve(in: snapshot).label, style: .callout))
				card.addArrangedSubview(label("Repairs: \(note.anchors.count - 1)", style: .caption1))
				card.addArrangedSubview(button("Repair note using selected passage", identifier: "notebook.repair.\(note.id)") { [weak self] in
					self?.repair(note)
				})
				card.addArrangedSubview(button("Remove note", identifier: "notebook.remove.\(note.id)") { [weak self] in self?.confirmRemoval(note) })
				notesStack.addArrangedSubview(card)
			}
		} catch {
			show(error)
		}
	}

	private func repair(_ note: NotebookNote) {
		do {
			try store.repair(id: note.id, snapshot: snapshot, selection: bodyText.selectedRange)
			feedback.text = "Attachment repaired. The original quote and prior anchor are retained."
			refreshNotes()
		} catch {
			show(error)
		}
	}

	private func confirmRemoval(_ note: NotebookNote) {
		let alert = UIAlertController(title: "Remove this note?", message: "This removes the local note and its repair history.", preferredStyle: .alert)
		alert.addAction(UIAlertAction(title: "Cancel", style: .cancel))
		alert.addAction(UIAlertAction(title: "Remove", style: .destructive) { [weak self] _ in
			guard let self else {
				return
			}
			do {
				try store.remove(id: note.id, key: snapshot.key)
				feedback.text = "Note removed."
				refreshNotes()
			} catch {
				show(error)
			}
		})
		present(alert, animated: true)
	}

	private func show(_ error: Error) {
		feedback.text = error.localizedDescription
		UIAccessibility.post(notification: .announcement, argument: feedback.text)
	}

	private func label(_ text: String, style: UIFont.TextStyle) -> UILabel {
		let label = UILabel()
		label.text = text
		label.numberOfLines = 0
		label.font = .preferredFont(forTextStyle: style)
		label.adjustsFontForContentSizeCategory = true
		return label
	}

	private func button(_ title: String, identifier: String, action: @escaping @MainActor () -> Void) -> UIButton {
		var configuration = UIButton.Configuration.tinted()
		configuration.title = title
		configuration.contentInsets = NSDirectionalEdgeInsets(top: 12, leading: 12, bottom: 12, trailing: 12)
		let button = UIButton(configuration: configuration, primaryAction: UIAction { _ in action() })
		button.accessibilityIdentifier = identifier
		button.titleLabel?.adjustsFontForContentSizeCategory = true
		return button
	}
}
