import SwiftUI

/// Sidebar showing the list of HORI projects.
///
/// Shows all projects from ProjectStore, with a "New Project" button.
/// Selecting a project sets it as the current project in WindowState.
///
/// Traces to: docs/roadmap.md MAC-5 (The Workshop).
struct ProjectSidebar: View {

    /// The project store (shared).
    let store: ProjectStore

    /// Currently selected project (binding to WindowState).
    @Binding var selectedProject: HoriProject?

    /// The list of projects (refreshed on appear and after creation).
    @State private var projects: [HoriProject] = []

    /// Whether the new project sheet is showing.
    @State private var showNewProjectSheet: Bool = false

    /// New project name being typed.
    @State private var newProjectName: String = ""

    /// Error message if project creation fails.
    @State private var errorMessage: String? = nil

    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                Text("Projects")
                    .font(HoriTypography.header)
                    .foregroundStyle(HoriTheme.text(for: colorScheme))
                Spacer()
                Button {
                    showNewProjectSheet = true
                } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("New project")
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(HoriTheme.surface(for: colorScheme))

            Divider()

            // Project list
            if projects.isEmpty {
                VStack(spacing: 8) {
                    Image(systemName: "folder.badge.plus")
                        .font(.system(size: 32))
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme).opacity(0.5))
                    Text("No projects yet")
                        .font(HoriTypography.caption)
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                    Text("Click + to create one")
                        .font(HoriTypography.caption)
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme).opacity(0.5))
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(projects) { project in
                            projectRow(project)
                        }
                    }
                }
            }

            // Error message (if any)
            if let error = errorMessage {
                Text(error)
                    .font(HoriTypography.caption)
                    .foregroundStyle(HoriTheme.semanticError)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 8)
            }
        }
        .background(HoriTheme.background(for: colorScheme))
        .onAppear { refreshProjects() }
        .sheet(isPresented: $showNewProjectSheet) {
            newProjectSheet
        }
    }

    // MARK: - Project Row

    private func projectRow(_ project: HoriProject) -> some View {
        let isSelected = selectedProject?.id == project.id

        return Button {
            selectedProject = project
        } label: {
            HStack(spacing: 10) {
                Image(systemName: "folder.fill")
                    .font(.system(size: 14))
                    .foregroundStyle(isSelected ? HoriTheme.semanticIdle : HoriTheme.textSecondary(for: colorScheme))

                VStack(alignment: .leading, spacing: 2) {
                    Text(project.name)
                        .font(HoriTypography.body)
                        .foregroundStyle(HoriTheme.text(for: colorScheme))
                        .lineLimit(1)

                    if !project.description.isEmpty {
                        Text(project.description)
                            .font(HoriTypography.caption)
                            .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                            .lineLimit(1)
                    }
                }

                Spacer()
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(isSelected ? HoriTheme.surface(for: colorScheme) : Color.clear)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Project: \(project.name)")
    }

    // MARK: - New Project Sheet

    private var newProjectSheet: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("New Project")
                .font(HoriTypography.header)
                .foregroundStyle(HoriTheme.text(for: colorScheme))

            VStack(alignment: .leading, spacing: 6) {
                Text("Name")
                    .font(HoriTypography.label)
                    .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))

                TextField("My Cool App", text: $newProjectName)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { createProject() }
            }

            HStack {
                Spacer()
                Button("Cancel") {
                    showNewProjectSheet = false
                    newProjectName = ""
                }
                .keyboardShortcut(.escape)

                Button("Create") {
                    createProject()
                }
                .keyboardShortcut(.return)
                .buttonStyle(.borderedProminent)
                .disabled(newProjectName.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 360)
        .background(HoriTheme.background(for: colorScheme))
    }

    // MARK: - Actions

    private func refreshProjects() {
        projects = (try? store.listProjects()) ?? []
    }

    private func createProject() {
        let name = newProjectName.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }

        do {
            let project = try store.createProject(name: name)
            refreshProjects()
            selectedProject = project
            showNewProjectSheet = false
            newProjectName = ""
            errorMessage = nil
        } catch {
            errorMessage = "Failed to create project: \(error.localizedDescription)"
        }
    }
}
