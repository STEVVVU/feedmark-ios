import Articles
import Foundation
import Testing
@testable import NetNewsWire

@MainActor @Suite struct ArticleStringFormatterSummaryCacheTests {

	@Test func changedBodyForSameArticleReturnsUpdatedSummary() {
		let formatter = ArticleStringFormatter()
		let original = makeArticle(body: "<p>Original introduction.</p>")
		let updated = makeArticle(body: "<p>New introduction. Original introduction.</p>")

		#expect(formatter.truncatedSummary(original) == "Original introduction.")
		#expect(formatter.truncatedSummary(updated) == "New introduction. Original introduction.")
	}

	@Test func summaryCacheKeepsAccountsSeparate() {
		let formatter = ArticleStringFormatter()
		let firstAccount = makeArticle(accountID: "first", body: "<p>First account.</p>")
		let secondAccount = makeArticle(accountID: "second", body: "<p>Second account.</p>")

		#expect(formatter.truncatedSummary(firstAccount) == "First account.")
		#expect(formatter.truncatedSummary(secondAccount) == "Second account.")
	}

	@Test func missingBodyDoesNotReuseCachedSummary() {
		let formatter = ArticleStringFormatter()
		let withBody = makeArticle(body: "<p>Cached content.</p>")
		let withoutBody = makeArticle(body: nil)

		#expect(formatter.truncatedSummary(withBody) == "Cached content.")
		#expect(formatter.truncatedSummary(withoutBody) == "")
	}
}

@MainActor private func makeArticle(accountID: String = "account1", body: String?) -> Article {
	let status = ArticleStatus(articleID: "article1", read: false, starred: false, dateArrived: Date())
	return Article(
		accountID: accountID,
		articleID: "article1",
		feedID: "feed1",
		uniqueID: "unique-article1",
		title: "Article",
		contentHTML: body,
		contentText: nil,
		markdown: nil,
		url: nil,
		externalURL: nil,
		summary: nil,
		imageURL: nil,
		datePublished: Date(),
		dateModified: nil,
		authors: nil,
		status: status
	)
}
