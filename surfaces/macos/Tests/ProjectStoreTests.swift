import Testing
import Foundation
@testable import HORI

/// Tests for ProjectStore — manages projects on disk at ~/HORI/projects/.
///
/// Uses a temporary directory for test isolation. Each test creates
/// its own ProjectStore with a temp root, so tests don't interfere
/// with each other or with real projects.
@Suite("ProjectStore", .serialized)
struct ProjectStoreTests {

    // MARK: - Project Creation

    @Test("Creates a project directory on disk")
    func createsProjectDirectory() throws {
        let store = ProjectStore(rootURL: makeTempDir())
        let project = try store.createProject(name: "My App")

        let projectDir = store.rootURL.appendingPathComponent(project.slug)
        #expect(FileManager.default.fileExists(atPath: projectDir.path))
    }

    @Test("Created project has correct name and slug")
    func createdProjectMetadata() throws {
        let store = ProjectStore(rootURL: makeTempDir())
        let project = try store.createProject(name: "My Cool Project")

        #expect(project.name == "My Cool Project")
        #expect(project.slug == "my-cool-project")
    }

    @Test("Create project generates README and hori.log")
    func createsDefaultFiles() throws {
        let store = ProjectStore(rootURL: makeTempDir())
        let project = try store.createProject(name: "Test Project")

        let projectDir = store.rootURL.appendingPathComponent(project.slug)
        let readme = projectDir.appendingPathComponent("README.md")
        let log = projectDir.appendingPathComponent("hori.log")

        #expect(FileManager.default.fileExists(atPath: readme.path))
        #expect(FileManager.default.fileExists(atPath: log.path))

        let readmeContent = try String(contentsOf: readme, encoding: .utf8)
        #expect(readmeContent.contains("Test Project"))
    }

    // MARK: - Project Listing

    @Test("Lists created projects")
    func listsProjects() throws {
        let store = ProjectStore(rootURL: makeTempDir())
        _ = try store.createProject(name: "Project A")
        _ = try store.createProject(name: "Project B")

        let projects = try store.listProjects()
        #expect(projects.count == 2)
        let names = projects.map(\.name).sorted()
        #expect(names == ["Project A", "Project B"])
    }

    @Test("Lists empty when no projects")
    func listsEmpty() throws {
        let store = ProjectStore(rootURL: makeTempDir())
        let projects = try store.listProjects()
        #expect(projects.isEmpty)
    }

    // MARK: - Slug Uniqueness

    @Test("Duplicate project names get unique slugs")
    func duplicateSlugs() throws {
        let store = ProjectStore(rootURL: makeTempDir())
        let p1 = try store.createProject(name: "My App")
        let p2 = try store.createProject(name: "My App")

        #expect(p1.slug != p2.slug)
        #expect(p1.slug == "my-app")
        #expect(p2.slug == "my-app-2")
    }

    // MARK: - File Operations

    @Test("Writes a file to a project")
    func writesFile() throws {
        let store = ProjectStore(rootURL: makeTempDir())
        let project = try store.createProject(name: "File Test")

        try store.writeFile(project: project, filename: "index.html", content: "<h1>Hi</h1>")

        let fileURL = store.rootURL
            .appendingPathComponent(project.slug)
            .appendingPathComponent("index.html")
        let content = try String(contentsOf: fileURL, encoding: .utf8)
        #expect(content == "<h1>Hi</h1>")
    }

    @Test("Creates subdirectories as needed")
    func createsSubdirectories() throws {
        let store = ProjectStore(rootURL: makeTempDir())
        let project = try store.createProject(name: "Sub Dir Test")

        try store.writeFile(project: project, filename: "src/main.js", content: "console.log('hi');")

        let fileURL = store.rootURL
            .appendingPathComponent(project.slug)
            .appendingPathComponent("src/main.js")
        #expect(FileManager.default.fileExists(atPath: fileURL.path))
    }

    @Test("Lists files in a project")
    func listsFiles() throws {
        let store = ProjectStore(rootURL: makeTempDir())
        let project = try store.createProject(name: "File List Test")

        try store.writeFile(project: project, filename: "index.html", content: "<h1>Hi</h1>")
        try store.writeFile(project: project, filename: "style.css", content: "body {}")
        try store.writeFile(project: project, filename: "src/app.js", content: "// app")

        let files = try store.listFiles(project: project)
        #expect(files.count >= 5)  // README.md, hori.log + 3 files
        let filenames = files.map(\.name)
        #expect(filenames.contains("index.html"))
        #expect(filenames.contains("style.css"))
        #expect(filenames.contains("app.js"))
    }

    // MARK: - Project Deletion

    @Test("Deletes a project directory")
    func deletesProject() throws {
        let store = ProjectStore(rootURL: makeTempDir())
        let project = try store.createProject(name: "Delete Me")

        try store.deleteProject(project)

        let projectDir = store.rootURL.appendingPathComponent(project.slug)
        #expect(!FileManager.default.fileExists(atPath: projectDir.path))
        let remaining = try store.listProjects()
        #expect(remaining.isEmpty)
    }

    // MARK: - Default Root

    @Test("Default root is ~/HORI/projects")
    func defaultRoot() {
        let store = ProjectStore()
        let expected = FileManager.default
            .homeDirectoryForCurrentUser
            .appendingPathComponent("HORI/projects")
        #expect(store.rootURL == expected)
    }

    // MARK: - Helpers

    private func makeTempDir() -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("hori-test-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }
}
