import Foundation

/// Manages HORI projects on disk.
///
/// Projects live at ~/HORI/projects/<slug>/. Each project directory
/// contains a README.md, hori.log, and any files HORI generates.
///
/// The store handles:
/// - Creating projects (with unique slugs)
/// - Listing projects
/// - Writing files (creating subdirectories as needed)
/// - Listing files in a project
/// - Deleting projects
///
/// Traces to: docs/roadmap.md MAC-5 (The Workshop).
final class ProjectStore {

    /// The root directory for all projects (~/HORI/projects by default).
    let rootURL: URL

    /// Initializes with the default root (~/HORI/projects).
    init() {
        self.rootURL = FileManager.default
            .homeDirectoryForCurrentUser
            .appendingPathComponent("HORI/projects")
        ensureRootExists()
    }

    /// Initializes with a custom root (for testing).
    init(rootURL: URL) {
        self.rootURL = rootURL
        ensureRootExists()
    }

    private func ensureRootExists() {
        try? FileManager.default.createDirectory(at: rootURL, withIntermediateDirectories: true)
    }

    // MARK: - Project CRUD

    /// Creates a new project with the given name.
    /// Generates a unique slug if the name collides with an existing project.
    /// - Parameter name: The human-readable project name.
    /// - Returns: The created HoriProject.
    /// - Throws: File system errors.
    func createProject(name: String) throws -> HoriProject {
        let slug = try uniqueSlug(for: name)
        let project = HoriProject(name: name, slug: slug)

        let projectDir = rootURL.appendingPathComponent(slug)
        try FileManager.default.createDirectory(at: projectDir, withIntermediateDirectories: true)

        // Create default files
        let readme = projectDir.appendingPathComponent("README.md")
        try "# \(name)\n\nCreated with HORI.\n".data(using: .utf8)?.write(to: readme)

        let log = projectDir.appendingPathComponent("hori.log")
        try "".data(using: .utf8)?.write(to: log)

        return project
    }

    /// Lists all projects in the root directory.
    /// - Returns: Array of projects sorted by name.
    /// - Throws: File system errors.
    func listProjects() throws -> [HoriProject] {
        let contents = try FileManager.default.contentsOfDirectory(at: rootURL, includingPropertiesForKeys: [.creationDateKey, .contentModificationDateKey])

        var projects: [HoriProject] = []
        for dir in contents {
            var isDir: ObjCBool = false
            guard FileManager.default.fileExists(atPath: dir.path, isDirectory: &isDir), isDir.boolValue else {
                continue
            }

            let slug = dir.lastPathComponent
            let name = slug.replacingOccurrences(of: "-", with: " ").capitalized

            let attrs = try? FileManager.default.attributesOfItem(atPath: dir.path)
            let created = (attrs?[.creationDate] as? Date) ?? Date()
            let modified = (attrs?[.modificationDate] as? Date) ?? Date()

            // Read README for description (first non-heading line)
            let readmeURL = dir.appendingPathComponent("README.md")
            var description = ""
            if let readmeContent = try? String(contentsOf: readmeURL, encoding: .utf8) {
                let lines = readmeContent.split(separator: "\n")
                description = lines.dropFirst().drop(while: { $0.trimmingCharacters(in: .whitespaces).isEmpty }).first.map(String.init) ?? ""
            }

            projects.append(HoriProject(
                name: name,
                slug: slug,
                created: created,
                modified: modified,
                description: description
            ))
        }

        return projects.sorted { $0.name < $1.name }
    }

    /// Deletes a project and all its files.
    /// - Parameter project: The project to delete.
    /// - Throws: File system errors.
    func deleteProject(_ project: HoriProject) throws {
        let projectDir = rootURL.appendingPathComponent(project.slug)
        try FileManager.default.removeItem(at: projectDir)
    }

    // MARK: - File Operations

    /// Writes a file to a project, creating subdirectories as needed.
    /// - Parameters:
    ///   - project: The project to write to.
    ///   - filename: Relative path within the project (e.g. "src/main.js").
    ///   - content: The file content.
    /// - Throws: File system errors.
    func writeFile(project: HoriProject, filename: String, content: String) throws {
        let projectDir = rootURL.appendingPathComponent(project.slug)
        let fileURL = projectDir.appendingPathComponent(filename)

        // Create parent directories if needed
        let parentDir = fileURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: parentDir, withIntermediateDirectories: true)

        try content.data(using: .utf8)?.write(to: fileURL)
    }

    /// Lists all files in a project, recursively.
    /// - Parameter project: The project to list files for.
    /// - Returns: Array of file entries (name + relative path).
    /// - Throws: File system errors.
    func listFiles(project: HoriProject) throws -> [ProjectFile] {
        let projectDir = rootURL.appendingPathComponent(project.slug)
        var files: [ProjectFile] = []

        let enumerator = FileManager.default.enumerator(at: projectDir, includingPropertiesForKeys: nil)
        while let fileURL = enumerator?.nextObject() as? URL {
            var isDir: ObjCBool = false
            guard FileManager.default.fileExists(atPath: fileURL.path, isDirectory: &isDir), !isDir.boolValue else {
                continue
            }

            let relativePath = fileURL.path.replacingOccurrences(of: projectDir.path + "/", with: "")
            files.append(ProjectFile(name: fileURL.lastPathComponent, path: relativePath))
        }

        return files.sorted { $0.path < $1.path }
    }

    // MARK: - Slug Generation

    /// Generates a unique slug for a project name.
    /// If "my-app" exists, tries "my-app-2", "my-app-3", etc.
    private func uniqueSlug(for name: String) throws -> String {
        let baseSlug = name
            .lowercased()
            .replacingOccurrences(of: " ", with: "-")
            .replacingOccurrences(of: "_", with: "-")
            // Remove non-alphanumeric characters
            .filter { $0.isLetter || $0.isNumber || $0 == "-" }

        let baseSlugSafe = baseSlug.isEmpty ? "untitled" : baseSlug

        // Check if base slug is available
        let baseDir = rootURL.appendingPathComponent(baseSlugSafe)
        if !FileManager.default.fileExists(atPath: baseDir.path) {
            return baseSlugSafe
        }

        // Try numbered suffixes
        var counter = 2
        while true {
            let candidate = "\(baseSlugSafe)-\(counter)"
            let candidateDir = rootURL.appendingPathComponent(candidate)
            if !FileManager.default.fileExists(atPath: candidateDir.path) {
                return candidate
            }
            counter += 1
        }
    }
}

// MARK: - Project File Model

/// A file within a HORI project.
struct ProjectFile: Identifiable, Equatable {
    let id = UUID()
    let name: String
    let path: String
}
